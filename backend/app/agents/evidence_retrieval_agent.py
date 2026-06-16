"""
Evidence Retrieval Agent — keyword-based document retrieval from stored manuals and SOPs.

Input  : state.machine_context, state.maintenance_history (for keyword extraction)
Output : AgentResult.data = {
    "retrieved_evidence": [
        {
            "source": str,           # filename
            "source_type": str,      # "manual" | "sop"
            "chunk": str,            # text passage
            "relevance_score": float # 0.0 – 1.0
        }
    ],
    "documents_searched": int,
    "chunks_evaluated": int
}

No RAG, no embeddings. Pure keyword overlap scoring.
"""

import re
from pathlib import Path
from typing import Any

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState

_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "storage" / "uploads"
_CHUNK_MIN_WORDS = 20
_CHUNK_MAX_WORDS = 120
_TOP_K = 8

# ── document cache ────────────────────────────────────────────────────────────
# Avoid reading and parsing the same document files on every agent call.
# Cache is invalidated when any file's modification time changes (new upload).
# This saves ~50-200ms per deep-analysis machine when 3+ machines share the
# same evidence corpus.

_DOC_CACHE: list[tuple[str, str, str]] | None = None
_DOC_CACHE_SNAPSHOT: frozenset[tuple[str, float]] = frozenset()


def _get_file_snapshot() -> frozenset[tuple[str, float]]:
    """Return (path, mtime) pairs for all document files — used as cache key."""
    snap: set[tuple[str, float]] = set()
    for source_type in ("manuals", "sops"):
        doc_dir = _STORAGE_ROOT / source_type
        if doc_dir.exists():
            for txt_file in doc_dir.glob("*.txt"):
                try:
                    snap.add((str(txt_file), txt_file.stat().st_mtime))
                except OSError:
                    pass
    return frozenset(snap)

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that",
    "this", "for", "with", "on", "at", "by", "be", "as", "are", "was",
    "were", "have", "has", "had", "from", "not", "but", "they", "their",
    "which", "when", "if", "all", "can", "will", "one", "do", "each",
    "than", "then", "into", "its", "also", "may", "should", "must",
}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _split_chunks(text: str) -> list[str]:
    """Split text into paragraph-level chunks within word count bounds."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        words = para.split()
        if len(words) < _CHUNK_MIN_WORDS:
            continue
        if len(words) <= _CHUNK_MAX_WORDS:
            chunks.append(para)
        else:
            # Slide a window over long paragraphs
            step = _CHUNK_MAX_WORDS // 2
            for i in range(0, len(words) - _CHUNK_MIN_WORDS, step):
                chunk = " ".join(words[i : i + _CHUNK_MAX_WORDS])
                chunks.append(chunk)
    return chunks


def _load_documents() -> list[tuple[str, str, str]]:
    """
    Returns list of (source_filename, source_type, full_text) tuples.

    Cache behaviour: documents are re-read only when the set of files or their
    modification times change. For a typical 15-machine run, 14 of the 15
    calls will be served from cache after the first machine loads the docs.
    """
    global _DOC_CACHE, _DOC_CACHE_SNAPSHOT
    current_snap = _get_file_snapshot()
    if _DOC_CACHE is not None and current_snap == _DOC_CACHE_SNAPSHOT:
        return _DOC_CACHE

    documents: list[tuple[str, str, str]] = []
    for source_type in ("manuals", "sops"):
        doc_dir = _STORAGE_ROOT / source_type
        if not doc_dir.exists():
            continue
        for txt_file in doc_dir.glob("*.txt"):
            try:
                text = txt_file.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    documents.append((txt_file.name, source_type.rstrip("s"), text))
            except OSError:
                continue

    _DOC_CACHE = documents
    _DOC_CACHE_SNAPSHOT = current_snap
    return _DOC_CACHE


def _build_query_keywords(state: VulcanOpsState) -> set[str]:
    keywords: set[str] = set()

    if state.machine_context:
        keywords |= _tokenize(state.machine_context.machine_type)
        keywords |= _tokenize(state.machine_context.machine_name)
        keywords |= _tokenize(state.machine_context.plant)
        keywords |= _tokenize(state.machine_context.location)

    # Add the current fault signal so manuals/SOPs about the specific symptom rank higher.
    if state.anomaly and state.anomaly.sensor:
        keywords |= _tokenize(state.anomaly.sensor)
        if state.anomaly.deviation_percent is not None and state.anomaly.deviation_percent > 0:
            keywords.add("high")
            keywords.add("elevated")

    for record in state.maintenance_history:
        keywords |= _tokenize(record.failure_mode)
        keywords |= _tokenize(record.action_taken)

    return keywords


def run(state: VulcanOpsState) -> AgentResult:
    documents = _load_documents()

    if not documents:
        return AgentResult(
            status="success",
            data={
                "retrieved_evidence": [],
                "documents_searched": 0,
                "chunks_evaluated": 0,
            },
            errors=["No manuals or SOPs found in storage. Upload documents to enable evidence retrieval."],
        )

    query_keywords = _build_query_keywords(state)

    if not query_keywords:
        return AgentResult(
            status="error",
            data={},
            errors=["Cannot build query: machine_context and maintenance_history are both empty"],
        )

    scored: list[dict[str, Any]] = []
    total_chunks = 0

    for filename, source_type, text in documents:
        chunks = _split_chunks(text)
        total_chunks += len(chunks)

        for chunk in chunks:
            chunk_keywords = _tokenize(chunk)
            if not chunk_keywords:
                continue
            overlap = query_keywords & chunk_keywords
            if not overlap:
                continue
            # Balanced F1-style score: rewards chunks that cover many query terms
            # (recall) while also being focused on those terms (precision).
            recall = len(overlap) / len(query_keywords)
            precision = len(overlap) / len(chunk_keywords)
            score = 2 * (precision * recall) / (precision + recall) if (precision + recall) else 0.0
            scored.append(
                {
                    "source": filename,
                    "source_type": source_type,
                    "chunk": chunk,
                    "relevance_score": round(score, 4),
                }
            )

    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    top = scored[:_TOP_K]

    return AgentResult(
        status="success",
        data={
            "retrieved_evidence": top,
            "documents_searched": len(documents),
            "chunks_evaluated": total_chunks,
        },
    )
