"""
Ingest Orchestration Service — 4-layer autonomous pipeline.

Execution order
───────────────
Layer 1 (Data)       — already complete when this function is called.
Layer 2 (Fast)       — run 5 non-LLM agents for ALL machines in parallel
                       (bounded by MAX_CONCURRENCY semaphore).
Layer 3 (Deep)       — run all 9 agents (incl. LLM) for the top MAX_DEEP_ANALYSIS
                       machines by risk score, sequentially to avoid rate limits.
Layer 4 (Cache)      — persist all results; chat and reports read from cache,
                       never re-running agents.

Timing targets
──────────────
Layer 2  : 5-10 s  (15 machines in parallel, no LLM)
Layer 3  : 20-40 s (3 machines × ~10 s each, 2 LLM calls per machine)
Total    : < 60 s

Public API
──────────
    await run_autonomous_pipeline(event_id, machine_ids)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import AsyncSessionLocal
from app.orchestrator.fast_pipeline import run_fast_agents
from app.orchestrator.pipeline_runner import (
    PipelineError,
    _load_machine,
    _load_maintenance_history,
    _load_sensor_readings,
    run_pipeline,
)
from app.core.state_contract import VulcanOpsState
from app.services import report_builder
from app.services.report_persistence_service import (
    mark_event_done,
    mark_event_failed,
    persist_batch,
    persist_fast_batch,
)

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

# ── tuneable constants ────────────────────────────────────────────────────────

# Concurrent machines in the fast layer (bounded to avoid DB connection pressure)
_MAX_CONCURRENCY = 5

# Number of highest-risk machines that receive full LLM deep analysis
_MAX_DEEP_ANALYSIS = 3


# ── helpers ───────────────────────────────────────────────────────────────────


async def _load_initial_state(
    machine_id: uuid.UUID,
    db_factory: async_sessionmaker,
) -> VulcanOpsState:
    """Open a fresh DB session, load machine + sensor + maintenance, return initial state."""
    async with db_factory() as db:
        machine_context = await _load_machine(machine_id, db)
        sensor_readings = await _load_sensor_readings(machine_id, db)
        maintenance_history = await _load_maintenance_history(machine_id, db)

    return VulcanOpsState(
        active_machine_id=machine_id,
        machine_context=machine_context,
        sensor_readings=sensor_readings,
        maintenance_history=maintenance_history,
    )


async def _fast_one(
    machine_id: uuid.UUID,
    db_factory: async_sessionmaker,
    semaphore: asyncio.Semaphore,
) -> tuple[VulcanOpsState, float] | None:
    """
    Load data and run fast agents for one machine, bounded by the semaphore.
    Returns (state, risk_score) or None on unrecoverable error.
    """
    async with semaphore:
        try:
            state = await _load_initial_state(machine_id, db_factory)
            # Fast agents are synchronous — run directly (no I/O, negligible CPU).
            state, risk_score = run_fast_agents(state)
            return state, risk_score
        except PipelineError as exc:
            logger.warning("Fast pipeline skipped machine %s: %s", machine_id, exc)
            return None
        except Exception as exc:
            logger.exception("Fast pipeline failed for machine %s: %s", machine_id, exc)
            return None


# ── main entry point ──────────────────────────────────────────────────────────


async def run_autonomous_pipeline(
    event_id: uuid.UUID,
    machine_ids: list[uuid.UUID],
    db_factory: async_sessionmaker | None = None,
) -> None:
    """
    Run the VulcanOps 4-layer pipeline for all machines in *machine_ids* and
    persist results.

    Designed to run as a FastAPI BackgroundTask. Manages its own DB sessions.

    Args:
        event_id:    The ingestion event that triggered this run.
        machine_ids: All machine UUIDs discovered during ingestion.
        db_factory:  Optional sessionmaker; defaults to the app-wide AsyncSessionLocal.
    """
    if db_factory is None:
        db_factory = AsyncSessionLocal

    if not machine_ids:
        async with db_factory() as db:
            await mark_event_done(event_id, db, machines_found=0)
        return

    # ── LAYER 2: Fast agents for ALL machines in parallel ─────────────────────
    t_pipeline_start = time.monotonic()
    t_layer2_start = t_pipeline_start

    logger.info(
        "event=%s | Layer 2 starting: %d machines, concurrency=%d",
        event_id, len(machine_ids), _MAX_CONCURRENCY,
    )
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
    fast_tasks = [
        _fast_one(mid, db_factory, semaphore)
        for mid in machine_ids
    ]
    fast_raw: list[tuple[VulcanOpsState, float] | None] = await asyncio.gather(*fast_tasks)

    t_layer2_end = time.monotonic()
    layer2_ms = round((t_layer2_end - t_layer2_start) * 1000, 1)

    # Filter out failures
    fast_results: list[tuple[VulcanOpsState, float]] = [r for r in fast_raw if r is not None]

    _PIPELINE_LOG.info(json.dumps({
        "event":            "layer_complete",
        "layer":            2,
        "event_id":         str(event_id),
        "machines_total":   len(machine_ids),
        "machines_ok":      len(fast_results),
        "machines_failed":  len(machine_ids) - len(fast_results),
        "latency_ms":       layer2_ms,
        "target_ms":        10000,
        "on_target":        layer2_ms <= 10000,
    }))

    if not fast_results:
        logger.error("event=%s | All fast pipelines failed — marking event failed", event_id)
        async with db_factory() as db:
            await mark_event_failed(event_id, db)
        return

    # ── RISK RANKING: sort by priority_score descending ───────────────────────
    fast_results.sort(key=lambda x: x[1], reverse=True)

    top_n = fast_results[:_MAX_DEEP_ANALYSIS]
    rest = fast_results[_MAX_DEEP_ANALYSIS:]

    top_ids = [str(s.active_machine_id) for s, _ in top_n]
    logger.info(
        "event=%s | Layer 2 done in %.0f ms. Risk ranking top-%d: %s",
        event_id, layer2_ms, _MAX_DEEP_ANALYSIS, top_ids,
    )

    # ── LAYER 3: Deep analysis for top-N machines (sequential, LLM) ──────────
    t_layer3_start = time.monotonic()
    logger.info("event=%s | Layer 3 starting: %d machines for deep analysis", event_id, len(top_n))
    deep_ok = 0
    deep_fail = 0

    for fast_state, risk_score in top_n:
        machine_id = fast_state.active_machine_id
        t_machine_start = time.monotonic()
        async with db_factory() as db:
            try:
                # run_pipeline re-runs all 9 agents (fast agents repeat in ~ms;
                # the LLM calls are what take time). This reuse keeps the finalize
                # logic, uncertainty override, and LangGraph invariants intact.
                full_state = await run_pipeline(str(machine_id), db)
                report = report_builder.build_single_report(full_state)
                await persist_batch(event_id, full_state, report, db)
                deep_ok += 1
                t_machine_ms = round((time.monotonic() - t_machine_start) * 1000, 1)
                _PIPELINE_LOG.info(json.dumps({
                    "event":       "machine_deep_analysis",
                    "event_id":    str(event_id),
                    "machine_id":  str(machine_id),
                    "risk_score":  round(risk_score, 2),
                    "status":      "success",
                    "latency_ms":  t_machine_ms,
                    "target_ms":   30000,
                    "on_target":   t_machine_ms <= 30000,
                }))
                logger.info("event=%s | Deep analysis done: machine=%s risk=%.1f latency=%.0f ms",
                            event_id, machine_id, risk_score, t_machine_ms)
            except Exception as exc:
                deep_fail += 1
                t_machine_ms = round((time.monotonic() - t_machine_start) * 1000, 1)
                _PIPELINE_LOG.info(json.dumps({
                    "event":       "machine_deep_analysis",
                    "event_id":    str(event_id),
                    "machine_id":  str(machine_id),
                    "risk_score":  round(risk_score, 2),
                    "status":      "error",
                    "latency_ms":  t_machine_ms,
                    "error":       str(exc),
                }))
                logger.exception("event=%s | Deep pipeline failed for machine=%s: %s", event_id, machine_id, exc)

    t_layer3_ms = round((time.monotonic() - t_layer3_start) * 1000, 1)
    _PIPELINE_LOG.info(json.dumps({
        "event":          "layer_complete",
        "layer":          3,
        "event_id":       str(event_id),
        "machines_total": len(top_n),
        "machines_ok":    deep_ok,
        "machines_failed": deep_fail,
        "latency_ms":     t_layer3_ms,
        "target_ms":      40000,
        "on_target":      t_layer3_ms <= 40000,
    }))

    # ── LAYER 3b: Persist fast-only results for the remaining machines ─────────
    fast_ok = 0
    fast_fail = 0

    for fast_state, risk_score in rest:
        machine_id = fast_state.active_machine_id
        async with db_factory() as db:
            try:
                await persist_fast_batch(event_id, fast_state, risk_score, db)
                fast_ok += 1
                logger.info("event=%s | Fast-only batch stored: machine=%s risk=%.1f", event_id, machine_id, risk_score)
            except Exception as exc:
                fast_fail += 1
                logger.exception("event=%s | Fast persist failed for machine=%s: %s", event_id, machine_id, exc)

    # ── Finalize event status ─────────────────────────────────────────────────
    total_ok = deep_ok + fast_ok
    total_fail = deep_fail + fast_fail
    t_total_ms = round((time.monotonic() - t_pipeline_start) * 1000, 1)

    _PIPELINE_LOG.info(json.dumps({
        "event":        "pipeline_complete",
        "event_id":     str(event_id),
        "deep_ok":      deep_ok,
        "deep_fail":    deep_fail,
        "fast_ok":      fast_ok,
        "fast_fail":    fast_fail,
        "total_ms":     t_total_ms,
        "target_ms":    60000,
        "on_target":    t_total_ms <= 60000,
    }))

    async with db_factory() as db:
        if total_ok == 0 and total_fail > 0:
            await mark_event_failed(event_id, db)
        else:
            await mark_event_done(event_id, db, machines_found=len(machine_ids))

    logger.info(
        "event=%s | Pipeline complete in %.0f ms: deep_ok=%d deep_fail=%d fast_ok=%d fast_fail=%d",
        event_id, t_total_ms, deep_ok, deep_fail, fast_ok, fast_fail,
    )
