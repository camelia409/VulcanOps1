"""Evidence Retrieval Agent — hybrid semantic + keyword search over ingested documents.

Phase 2 upgrade: queries the document_chunks table in PostgreSQL via pgvector
cosine-similarity search, then re-ranks with a keyword overlap boost.

Retrieval strategy (two-stage):
  Stage 1 — Vector search (semantic):
    Embed the query → cosine similarity against document_chunks.embedding
    → top 20 candidates.  Finds "thermal stress" when query says "heat anomaly".

  Stage 2 — Keyword boost (lexical):
    F1 overlap between query keywords and each candidate chunk.
    Adds a bonus to candidates that also contain exact query terms.

  Final score = 0.7 × cosine_similarity + 0.3 × keyword_f1

  Fallback: if pgvector is unavailable or embeddings are null, falls back to
  pure keyword F1 over all chunks in the DB.  Keyword-only is also used when
  the embedding model hasn't loaded yet (e.g. cold start).

Input  : state.machine_context, state.anomaly, state.maintenance_history
         (Retrieval uses a fresh DB session via AsyncSessionLocal)
Output : AgentResult.data = {
    "retrieved_evidence": [
        {
            "source": str,
            "source_type": str,
            "chunk": str,
            "relevance_score": float,
            "retrieval_method": "hybrid" | "keyword"
        }
    ],
    "documents_searched": int,
    "chunks_evaluated": int,
}
"""

import re
import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk
from app.services import embedding_service

logger = logging.getLogger(__name__)

_TOP_K_VECTOR = 20      # candidates from vector search
_TOP_K_FINAL = 8        # returned to the pipeline
_VECTOR_WEIGHT = 0.7
_KEYWORD_WEIGHT = 0.3

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that",
    "this", "for", "with", "on", "at", "by", "be", "as", "are", "was",
    "were", "have", "has", "had", "from", "not", "but", "they", "their",
    "which", "when", "if", "all", "can", "will", "one", "do", "each",
    "than", "then", "into", "its", "also", "may", "should", "must",
}


def _tokenize(text_str: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]{3,}", text_str.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _keyword_f1(query_kw: set[str], chunk_kw: set[str]) -> float:
    if not query_kw or not chunk_kw:
        return 0.0
    overlap = query_kw & chunk_kw
    if not overlap:
        return 0.0
    precision = len(overlap) / len(chunk_kw)
    recall = len(overlap) / len(query_kw)
    return 2 * precision * recall / (precision + recall)


def _build_query(state: VulcanOpsState) -> tuple[str, set[str]]:
    """Build a rich query string and keyword set from the current machine state."""
    parts: list[str] = []
    keywords: set[str] = set()

    if state.machine_context:
        m = state.machine_context
        parts.append(f"{m.machine_type} {m.machine_name} maintenance failure")
        keywords |= _tokenize(m.machine_type)
        keywords |= _tokenize(m.machine_name)

    if state.anomaly and state.anomaly.detected and state.anomaly.sensor:
        sensor = state.anomaly.sensor
        parts.append(f"{sensor} anomaly threshold exceedance inspection")
        keywords |= _tokenize(sensor)
        keywords.update({"anomaly", "threshold", "exceedance", "inspection", "failure"})

    for record in state.maintenance_history[:3]:
        if record.failure_mode:
            parts.append(record.failure_mode)
            keywords |= _tokenize(record.failure_mode)
        if record.action_taken:
            keywords |= _tokenize(record.action_taken)

    query_text = ". ".join(parts) if parts else "industrial equipment maintenance failure inspection"
    return query_text, keywords


async def _vector_search(
    query_embedding: list[float],
    query_keywords: set[str],
    db: AsyncSession,
    top_k: int,
) -> list[dict[str, Any]]:
    """
    Cosine similarity search in pgvector.

    asyncpg does not support the '::' PostgreSQL cast syntax inside parameterized
    queries, so we inline the vector literal directly.  The value is our own
    float list (not user input), so this is safe.
    """
    # Format as pgvector text literal: '[0.1,0.2,...]'
    vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"

    sql = text(
        "SELECT chunk_id, source_filename, source_type, chunk_text, "
        f"       1.0 - (embedding::vector <=> '{vec_literal}'::vector) AS similarity "
        "FROM document_chunks "
        "WHERE embedding IS NOT NULL "
        f"ORDER BY embedding::vector <=> '{vec_literal}'::vector "
        f"LIMIT {top_k}"
    )
    result = await db.execute(sql)
    rows = result.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        chunk_kw = _tokenize(row.chunk_text)
        kw_score = _keyword_f1(query_keywords, chunk_kw)
        hybrid_score = _VECTOR_WEIGHT * float(row.similarity) + _KEYWORD_WEIGHT * kw_score
        results.append({
            "source": row.source_filename,
            "source_type": row.source_type,
            "chunk": row.chunk_text,
            "relevance_score": round(hybrid_score, 4),
            "retrieval_method": "hybrid",
        })

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results


async def _keyword_search(
    query_keywords: set[str],
    db: AsyncSession,
    top_k: int,
) -> list[dict[str, Any]]:
    """Pure keyword F1 fallback — used when embeddings are not available."""
    result = await db.execute(
        select(
            DocumentChunk.source_filename,
            DocumentChunk.source_type,
            DocumentChunk.chunk_text,
        )
    )
    rows = result.fetchall()

    scored: list[dict[str, Any]] = []
    for row in rows:
        chunk_kw = _tokenize(row.chunk_text)
        score = _keyword_f1(query_keywords, chunk_kw)
        if score > 0:
            scored.append({
                "source": row.source_filename,
                "source_type": row.source_type,
                "chunk": row.chunk_text,
                "relevance_score": round(score, 4),
                "retrieval_method": "keyword",
            })

    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    return scored[:top_k]


async def run(state: VulcanOpsState) -> AgentResult:
    """
    Async evidence retrieval — opens its own DB session so the agent graph
    does not need to pass sessions through state.
    """
    query_text, query_keywords = _build_query(state)

    async with AsyncSessionLocal() as db:
        # Count available chunks for metadata.
        count_result = await db.execute(select(func.count(DocumentChunk.chunk_id)))
        total_chunks: int = count_result.scalar_one()

        if total_chunks == 0:
            return AgentResult(
                status="success",
                data={
                    "retrieved_evidence": [],
                    "documents_searched": 0,
                    "chunks_evaluated": 0,
                },
                errors=["No documents in the semantic index. Upload manuals and SOPs to enable evidence retrieval."],
            )

        # Try vector search first.
        query_embedding = await embedding_service.embed(query_text)

        if query_embedding is not None:
            try:
                results = await _vector_search(query_embedding, query_keywords, db, _TOP_K_VECTOR)
                results = results[:_TOP_K_FINAL]
                logger.info(
                    "Vector search returned %d results for query %r",
                    len(results), query_text[:60],
                )
            except Exception as exc:
                logger.warning("Vector search failed (%s) — falling back to keyword", exc)
                # asyncpg leaves the transaction in an aborted state after a SQL
                # error.  Roll back so the keyword fallback query can run cleanly.
                await db.rollback()
                results = await _keyword_search(query_keywords, db, _TOP_K_FINAL)
        else:
            # Embedding model not loaded — pure keyword fallback.
            results = await _keyword_search(query_keywords, db, _TOP_K_FINAL)

        # Count distinct source files in results.
        docs_searched = len({r["source"] for r in results})

        return AgentResult(
            status="success",
            data={
                "retrieved_evidence": results,
                "documents_searched": docs_searched,
                "chunks_evaluated": total_chunks,
            },
        )
