"""
Fast Intelligence Layer — run the 5 non-LLM agents on a single machine.

Called once per machine by the ingest orchestrator with asyncio.gather()
so all machines run concurrently (bounded by a semaphore).

No LangGraph overhead — agents are called directly and state is updated
via model_copy() between each step.

Public API
----------
    state, risk_score = run_fast_agents(initial_state)

Returns
-------
    state      : VulcanOpsState with anomaly, rul_prediction, retrieved_evidence,
                 impact, and priority populated.
    risk_score : float 0-100 from plant_priority_agent.priority_score;
                 higher = more urgent; used for risk ranking before deep analysis.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any

from app.agents import (
    anomaly_agent,
    evidence_retrieval_agent,
    operational_impact_agent,
    plant_priority_agent,
    prognostics_agent,
)
from app.agents.base import AgentResult
from app.core.enums import MaintenancePriority, RiskLevel
from app.core.state_contract import (
    AnomalyDetail,
    ImpactAssessment,
    RULPrediction,
    VulcanOpsState,
)
from app.orchestrator.execution_trace import build_trace, now_utc

logger = logging.getLogger(__name__)

_RANK_TO_PRIORITY: dict[str, MaintenancePriority] = {
    "P1": MaintenancePriority.EMERGENCY,
    "P2": MaintenancePriority.URGENT,
    "P3": MaintenancePriority.SCHEDULED,
    "P4": MaintenancePriority.ROUTINE,
}


def _call(agent_module: Any, state: VulcanOpsState) -> tuple[AgentResult, dict[str, Any]]:
    """Invoke an agent (sync or async), catch exceptions, return (result, trace_entry)."""
    agent_name = agent_module.__name__.rsplit(".", 1)[-1]
    start = now_utc()
    try:
        maybe_coro = agent_module.run(state)
        if inspect.isawaitable(maybe_coro):
            loop = asyncio.new_event_loop()
            try:
                result: AgentResult = loop.run_until_complete(maybe_coro)
            finally:
                loop.close()
        else:
            result = maybe_coro
    except Exception as exc:
        logger.warning("Fast agent %s raised: %s", agent_name, exc)
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()
    trace = build_trace(agent_name, start, end, result.status)
    return result, trace


def run_fast_agents(state: VulcanOpsState) -> tuple[VulcanOpsState, float]:
    """
    Run all 5 non-LLM agents against *state* and return the updated state
    together with a 0-100 risk score for fleet-level risk ranking.

    The returned state has: anomaly, rul_prediction, retrieved_evidence,
    impact, priority, execution_trace, errors.

    Fields left None: diagnosis, verification, strategy, role_reports, final_report.
    Those are populated only for high-risk machines by the deep pipeline.
    """
    traces: list[dict[str, Any]] = list(state.execution_trace)
    errors: list[dict[str, Any]] = list(state.errors)
    risk_score = 0.0

    # ── 1. Anomaly Detection ──────────────────────────────────────────────────
    result, trace = _call(anomaly_agent, state)
    traces.append(trace)
    if result.status == "success":
        d = result.data
        primary = d["anomalies"][0] if d["anomalies"] else None
        anomaly = AnomalyDetail(
            detected=d["anomaly_detected"],
            sensor=primary["sensor"] if primary else None,
            value=primary["value"] if primary else None,
            threshold=primary["threshold"] if primary else None,
            deviation_percent=primary["deviation_percent"] if primary else None,
            detected_at=datetime.fromisoformat(primary["detected_at"]) if primary else None,
        )
        state = state.model_copy(update={"anomaly": anomaly, "execution_trace": traces, "errors": errors})
    else:
        errors.append({"agent": "anomaly_agent", "errors": result.errors})
        state = state.model_copy(update={"execution_trace": traces, "errors": errors})

    # ── 2. Prognostics (RUL) ─────────────────────────────────────────────────
    result, trace = _call(prognostics_agent, state)
    traces.append(trace)
    if result.status == "success":
        d = result.data
        rul = RULPrediction(
            remaining_useful_life_hours=d.get("hours_remaining"),
            confidence=d.get("confidence"),
            basis=d.get("basis"),
        )
        state = state.model_copy(update={"rul_prediction": rul, "execution_trace": traces, "errors": errors})
    else:
        errors.append({"agent": "prognostics_agent", "errors": result.errors})
        state = state.model_copy(update={"execution_trace": traces, "errors": errors})

    # ── 3. Evidence Retrieval ─────────────────────────────────────────────────
    result, trace = _call(evidence_retrieval_agent, state)
    traces.append(trace)
    if result.status == "success":
        state = state.model_copy(update={
            "retrieved_evidence": result.data.get("retrieved_evidence", []),
            "execution_trace": traces,
            "errors": errors,
        })
    else:
        errors.append({"agent": "evidence_retrieval_agent", "errors": result.errors})
        state = state.model_copy(update={"execution_trace": traces, "errors": errors})

    # ── 4. Operational Impact ─────────────────────────────────────────────────
    result, trace = _call(operational_impact_agent, state)
    traces.append(trace)
    if result.status == "success":
        d = result.data
        impact = ImpactAssessment(
            risk_level=RiskLevel(d["risk_level"]),
            estimated_downtime_hours=d.get("estimated_downtime_hours"),
            estimated_cost_usd=d.get("estimated_cost_usd"),
            affected_production_lines=d.get("affected_production_lines", []),
            compliance_flags=d.get("compliance_flags", []),
            business_impact_summary=d.get("business_impact_summary"),
        )
        state = state.model_copy(update={"impact": impact, "execution_trace": traces, "errors": errors})
    else:
        errors.append({"agent": "operational_impact_agent", "errors": result.errors})
        state = state.model_copy(update={"execution_trace": traces, "errors": errors})

    # ── 5. Plant Priority (provides risk score) ───────────────────────────────
    result, trace = _call(plant_priority_agent, state)
    traces.append(trace)
    if result.status == "success":
        d = result.data
        priority = _RANK_TO_PRIORITY.get(d.get("priority_rank", "P3"), MaintenancePriority.SCHEDULED)
        risk_score = float(d.get("priority_score", 0.0))
        state = state.model_copy(update={"priority": priority, "execution_trace": traces, "errors": errors})
    else:
        errors.append({"agent": "plant_priority_agent", "errors": result.errors})
        state = state.model_copy(update={"execution_trace": traces, "errors": errors})

    return state, risk_score
