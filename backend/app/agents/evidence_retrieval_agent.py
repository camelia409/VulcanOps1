"""Evidence Retrieval Agent — multi-hop ReAct agent for document evidence retrieval.

Upgraded from deterministic single-shot hybrid search to a 3-iteration ReAct loop.
The agent can reformulate queries using technical industrial vocabulary and make
follow-up searches when the first pass returns weak or off-topic results.

Tools:
  search_documents(query, top_k=5) — hybrid pgvector cosine + keyword F1 search
  conclude_retrieval(chunk_ids, reasoning) — terminal: select the best chunks

Loop cap: _MAX_ITERATIONS = 3

Fallback: on LLMError falls back to the original single-shot hybrid search so the
diagnosis agent always has at least baseline documentary grounding.

Retrieval strategy (two-stage, unchanged from deterministic version):
  Stage 1 — Vector search (semantic, pgvector cosine similarity, top 20 candidates)
  Stage 2 — Keyword boost (F1 overlap between query terms and chunk terms)
  Final score = 0.7 × cosine_similarity + 0.3 × keyword_f1

Output shape (identical to previous version — downstream consumers unchanged):
  retrieved_evidence: list[{source, source_type, chunk, relevance_score, retrieval_method}]
  query_history: list[str]   ← new field: queries attempted in this run, in order
  llm_telemetry: {...}
  documents_searched: int
  chunks_evaluated: int
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk
from app.services import embedding_service
from app.services.llm_service import LLMError, llm_service

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 3
_TOP_K_VECTOR = 20      # candidates from vector search per call
_TOP_K_TOOL = 5         # returned per search_documents call
_TOP_K_FINAL = 8        # max chunks in retrieved_evidence
_VECTOR_WEIGHT = 0.7
_KEYWORD_WEIGHT = 0.3

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that",
    "this", "for", "with", "on", "at", "by", "be", "as", "are", "was",
    "were", "have", "has", "had", "from", "not", "but", "they", "their",
    "which", "when", "if", "all", "can", "will", "one", "do", "each",
    "than", "then", "into", "its", "also", "may", "should", "must",
}

# ── Tool schema ──────────────────────────────────────────────────────────────

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": (
                "Search ingested manuals, SOPs, and documents using semantic + keyword matching. "
                "Use precise technical vocabulary: part names, failure modes, component types. "
                "Returns a list of matching chunks with IDs and relevance scores."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query in technical industrial vocabulary.",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Number of chunks to return (default 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conclude_retrieval",
            "description": (
                "Finish retrieval. Pass the chunk_ids of the most relevant chunks found "
                "across all searches, and a one-sentence explanation of why they are relevant. "
                "chunk_ids must come from previous search_documents results — do not invent IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chunk_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of the selected chunks from search_documents results.",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "One sentence explaining why these chunks are relevant.",
                    },
                },
                "required": ["chunk_ids", "reasoning"],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are an industrial document retrieval specialist. "
    "Your task is to find the most relevant documentation evidence for a diagnostic question.\n\n"
    "WORKFLOW:\n"
    "1. Start by calling search_documents with the diagnostic query.\n"
    "2. Review the results. If the scores are low (< 0.3), content is off-topic, or too few chunks "
    "were returned, reformulate using more specific technical terms and search again "
    "(e.g. 'bearing seizure lubrication' instead of 'mechanical failure').\n"
    "3. When you have enough relevant evidence, call conclude_retrieval with the best chunk_ids.\n\n"
    "RULES:\n"
    "- Always call search_documents at least once before calling conclude_retrieval.\n"
    "- You have at most 3 iterations total. Do not waste turns repeating the same query.\n"
    "- conclude_retrieval chunk_ids must come ONLY from chunk_ids returned by search_documents. "
    "Never invent chunk IDs.\n"
    "- If all searches return empty or irrelevant results, call conclude_retrieval with an empty "
    "chunk_ids list and explain in reasoning."
)

# ── Low-level search helpers (same logic as before) ──────────────────────────


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


async def _vector_search(
    query_embedding: list[float],
    query_keywords: set[str],
    db: AsyncSession,
    top_k_vector: int,
    top_k_final: int,
) -> list[dict[str, Any]]:
    vec_literal = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"
    sql = text(
        "SELECT chunk_id, source_filename, source_type, chunk_text, "
        f"       1.0 - (embedding::vector <=> '{vec_literal}'::vector) AS similarity "
        "FROM document_chunks "
        "WHERE embedding IS NOT NULL "
        f"ORDER BY embedding::vector <=> '{vec_literal}'::vector "
        f"LIMIT {top_k_vector}"
    )
    result = await db.execute(sql)
    rows = result.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        chunk_kw = _tokenize(row.chunk_text)
        kw_score = _keyword_f1(query_keywords, chunk_kw)
        hybrid_score = _VECTOR_WEIGHT * float(row.similarity) + _KEYWORD_WEIGHT * kw_score
        results.append({
            "chunk_id": str(row.chunk_id),
            "source": row.source_filename,
            "source_type": row.source_type,
            "chunk": row.chunk_text,
            "relevance_score": round(hybrid_score, 4),
            "retrieval_method": "hybrid",
        })

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results[:top_k_final]


async def _keyword_search(
    query_keywords: set[str],
    db: AsyncSession,
    top_k: int,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(
            DocumentChunk.chunk_id,
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
                "chunk_id": str(row.chunk_id),
                "source": row.source_filename,
                "source_type": row.source_type,
                "chunk": row.chunk_text,
                "relevance_score": round(score, 4),
                "retrieval_method": "keyword",
            })

    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    return scored[:top_k]


async def _search(query: str, top_k: int, db: AsyncSession) -> list[dict[str, Any]]:
    """Execute one hybrid search for the given query. Returns chunk dicts with chunk_id."""
    query_keywords = _tokenize(query)
    query_embedding = await embedding_service.embed(query)

    if query_embedding is not None:
        try:
            return await _vector_search(query_embedding, query_keywords, db, _TOP_K_VECTOR, top_k)
        except Exception as exc:
            logger.warning("Vector search failed (%s) — falling back to keyword", exc)
            await db.rollback()
    return await _keyword_search(query_keywords, db, top_k)


def _build_fallback_query(state: VulcanOpsState) -> str:
    """Build a rich query string from state for the deterministic fallback."""
    parts: list[str] = []
    if state.machine_context:
        m = state.machine_context
        parts.append(f"{m.machine_type} {m.machine_name} maintenance failure")
    if state.anomaly and state.anomaly.detected and state.anomaly.sensor:
        parts.append(f"{state.anomaly.sensor} anomaly threshold exceedance inspection")
    for record in state.maintenance_history[:3]:
        if record.failure_mode:
            parts.append(record.failure_mode)
    return ". ".join(parts) if parts else "industrial equipment maintenance failure inspection"


def _build_initial_message(state: VulcanOpsState) -> str:
    """Build the initial user message for the ReAct loop."""
    machine = state.machine_context
    machine_desc = (
        f"{machine.machine_name} ({machine.machine_type})"
        if machine else "Unknown machine"
    )
    failure_mode = "unknown"
    for rec in state.maintenance_history[:1]:
        if rec.failure_mode:
            failure_mode = rec.failure_mode
            break
    if state.anomaly and state.anomaly.detected and state.anomaly.sensor:
        anomaly_desc = f"Sensor {state.anomaly.sensor} exceeded threshold by {state.anomaly.deviation_percent or 0:.1f}%"
    else:
        anomaly_desc = "No anomaly detected"

    return (
        f"Diagnostic evidence retrieval task.\n\n"
        f"MACHINE: {machine_desc}\n"
        f"ANOMALY: {anomaly_desc}\n"
        f"LAST KNOWN FAILURE MODE: {failure_mode}\n\n"
        f"Find the most relevant documentation evidence in the document corpus "
        f"for this diagnostic question. Start with a search."
    )


def _format_search_observation(chunks: list[dict[str, Any]]) -> str:
    """Format search results as a tool observation for the LLM."""
    if not chunks:
        return "No matching documents found."
    lines = [f"Found {len(chunks)} chunks:"]
    for i, ch in enumerate(chunks):
        score = ch["relevance_score"]
        source = ch["source"]
        chunk_id = ch["chunk_id"]
        snippet = ch["chunk"][:300].replace("\n", " ")
        lines.append(
            f"[{i}] chunk_id={chunk_id} source={source} score={score:.3f}\n"
            f"    {snippet}"
        )
    return "\n".join(lines)


# ── Main run function ─────────────────────────────────────────────────────────


async def run(state: VulcanOpsState) -> AgentResult:
    """
    Multi-hop ReAct evidence retrieval.
    Opens its own DB session for the full loop.
    """
    async with AsyncSessionLocal() as db:
        # Check if any documents exist at all.
        count_result = await db.execute(select(func.count(DocumentChunk.chunk_id)))
        total_chunks: int = count_result.scalar_one()

        if total_chunks == 0:
            return AgentResult(
                status="success",
                data={
                    "retrieved_evidence": [],
                    "query_history": [],
                    "documents_searched": 0,
                    "chunks_evaluated": 0,
                    "llm_telemetry": {"fallback_used": True, "reason": "no_documents"},
                },
                errors=["No documents in the semantic index. Upload manuals and SOPs to enable evidence retrieval."],
            )

        # Accumulated chunk pool: chunk_id → chunk dict (deduplicates across searches)
        chunk_pool: dict[str, dict[str, Any]] = {}
        query_history: list[str] = []
        telemetry_calls: list[dict[str, Any]] = []
        concluded_ids: list[str] = []
        concluded_reasoning: str = ""

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _build_initial_message(state)},
        ]

        # ── ReAct loop ────────────────────────────────────────────────────────
        try:
            for iteration in range(1, _MAX_ITERATIONS + 1):
                llm_result = await llm_service.call_with_tools(
                    agent="evidence_retrieval_agent",
                    system=_SYSTEM_PROMPT,
                    messages=messages,
                    tools=_TOOLS,
                )

                telemetry_calls.append({"iteration": iteration, "kind": llm_result.kind})
                thought = llm_result.content or "(no narration)"

                # ── model returned final text instead of a tool call ──────────
                if llm_result.kind == "final":
                    logger.warning(
                        "[evidence_retrieval_agent] iteration=%d returned final text "
                        "instead of a tool call — nudging", iteration,
                    )
                    if iteration < _MAX_ITERATIONS:
                        messages.append(
                            {"role": "assistant", "content": thought}
                        )
                        messages.append(
                            {"role": "user", "content": "You must call a tool (search_documents or conclude_retrieval)."}
                        )
                        continue
                    break

                action = llm_result.tool_name or ""
                action_input = llm_result.tool_args or {}
                tool_call_id = llm_result.tool_call_id or f"synthetic-{iteration}"

                assistant_tool_call = {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {"name": action, "arguments": json.dumps(action_input)},
                }
                messages.append({
                    "role": "assistant",
                    "content": thought if thought != "(no narration)" else "",
                    "tool_calls": [assistant_tool_call],
                })

                # ── conclude_retrieval ────────────────────────────────────────
                if action == "conclude_retrieval":
                    concluded_ids = action_input.get("chunk_ids", [])
                    concluded_reasoning = action_input.get("reasoning", "")
                    print(
                        f"[evidence_retrieval_agent] iteration={iteration} "
                        f"action=conclude_retrieval ids={concluded_ids} "
                        f"reasoning={concluded_reasoning[:80]!r}",
                        flush=True,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "Retrieval concluded.",
                    })
                    break

                # ── search_documents ──────────────────────────────────────────
                if action == "search_documents":
                    query = action_input.get("query", "").strip()
                    top_k = int(action_input.get("top_k", _TOP_K_TOOL))
                    top_k = max(1, min(top_k, 10))

                    if not query or len(query.split()) < 2:
                        observation = "Query too short — please provide a more descriptive search query."
                    else:
                        query_history.append(query)
                        chunks = await _search(query, top_k, db)
                        # Merge into pool
                        for ch in chunks:
                            chunk_pool[ch["chunk_id"]] = ch
                        observation = _format_search_observation(chunks)

                    print(
                        f"[evidence_retrieval_agent] iteration={iteration} "
                        f"action=search_documents query={query!r} "
                        f"hits={len(chunk_pool)} pool_size={len(chunk_pool)}",
                        flush=True,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": observation,
                    })
                    continue

                # ── unknown tool name ─────────────────────────────────────────
                observation = f"Unknown tool '{action}'. Call search_documents or conclude_retrieval."
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": observation,
                })

        except LLMError as exc:
            # LLM unavailable — fall back to single-shot deterministic search
            logger.warning(
                "[evidence_retrieval_agent] LLM unavailable (%s) — falling back to deterministic search",
                type(exc).__name__,
            )
            fallback_query = _build_fallback_query(state)
            query_keywords = _tokenize(fallback_query)
            query_embedding = await embedding_service.embed(fallback_query)
            if query_embedding is not None:
                try:
                    fallback_chunks = await _vector_search(
                        query_embedding, query_keywords, db, _TOP_K_VECTOR, _TOP_K_FINAL
                    )
                except Exception:
                    await db.rollback()
                    fallback_chunks = await _keyword_search(query_keywords, db, _TOP_K_FINAL)
            else:
                fallback_chunks = await _keyword_search(query_keywords, db, _TOP_K_FINAL)

            docs_searched = len({c["source"] for c in fallback_chunks})
            evidence = [{k: v for k, v in c.items() if k != "chunk_id"} for c in fallback_chunks]
            return AgentResult(
                status="success",
                data={
                    "retrieved_evidence": evidence,
                    "query_history": [fallback_query],
                    "documents_searched": docs_searched,
                    "chunks_evaluated": total_chunks,
                    "llm_telemetry": {
                        "fallback_used": True,
                        "reason": type(exc).__name__,
                        "calls": [],
                    },
                },
            )

        # ── resolve concluded_ids → final evidence list ───────────────────────
        if concluded_ids:
            # Keep only valid ids from the pool, in the order the agent selected
            selected = [chunk_pool[cid] for cid in concluded_ids if cid in chunk_pool]
            if not selected and chunk_pool:
                # Agent hallucinated IDs — fall back to best-scoring pool chunks
                logger.warning(
                    "[evidence_retrieval_agent] conclude_retrieval had no valid chunk_ids "
                    "— using top pool chunks as fallback"
                )
                selected = sorted(chunk_pool.values(), key=lambda x: x["relevance_score"], reverse=True)
        elif chunk_pool:
            # Loop exhausted without a conclude call — use best-scoring pool chunks
            logger.warning(
                "[evidence_retrieval_agent] loop exhausted without conclude_retrieval "
                "— using top pool chunks"
            )
            selected = sorted(chunk_pool.values(), key=lambda x: x["relevance_score"], reverse=True)
        else:
            selected = []

        # Strip chunk_id from the final evidence (downstream consumers don't expect it)
        evidence = [
            {k: v for k, v in ch.items() if k != "chunk_id"}
            for ch in selected[:_TOP_K_FINAL]
        ]

        docs_searched = len({ch.get("source") for ch in evidence})
        merged_telemetry: dict[str, Any] = {
            "calls": telemetry_calls,
            "iterations": len(telemetry_calls),
            "fallback_used": False,
            "concluded_reasoning": concluded_reasoning,
        }

        return AgentResult(
            status="success",
            data={
                "retrieved_evidence": evidence,
                "query_history": query_history,
                "documents_searched": docs_searched,
                "chunks_evaluated": total_chunks,
                "llm_telemetry": merged_telemetry,
            },
        )
