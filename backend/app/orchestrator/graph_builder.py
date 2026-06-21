"""
LangGraph orchestration graph for the VulcanOps reliability pipeline.

Topology (linear):
    START → anomaly → prognostics → evidence_retrieval → diagnosis
          → evidence_verification → operational_impact → maintenance_strategy
          → plant_priority → communication → finalize → END

Invariants enforced as node guards (skipped nodes are traced, not crashed):
    1. diagnosis        requires state.anomaly is not None
    2. verification     requires state.diagnosis is not None
    3. communication    requires state.strategy is not None
    4. finalize asserts diagnosis is non-empty when sensor data exists
    5. finalize blocks  final_report construction when root_cause is missing
"""

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

# Optional progress callback injected by run_pipeline for job-stage tracking.
# Stored in a context variable so the compiled LangGraph nodes can report
# progress without polluting VulcanOpsState or the global graph singleton.
_ProgressCallback = Callable[[str], Awaitable[None]] | None
_progress_callback_ctx: contextvars.ContextVar[_ProgressCallback] = contextvars.ContextVar(
    "_progress_callback_ctx", default=None
)

# ── uncertain-diagnosis text sanitizer ───────────────────────────────────────

# Only strip language that implies certainty when the diagnosis is uncertain.
# Concrete maintenance terms (bearing wear, replace bearings, LOTO) are allowed
# when evidence supports them; they are only suppressed for uncertain cases.
_UNCERTAINTY_PATTERN = re.compile(
    r"confirmed\b|"
    r"definitely\s+is\b|"
    r"certainly\s+is\b|"
    r"proven\s+to\s+be\b|"
    r"supported by available evidence\b",
    re.IGNORECASE,
)


def _sanitize_uncertain_text(text: str) -> str:
    """Replace certainty-claim phrasing that contradicts an uncertain diagnosis."""
    return _UNCERTAINTY_PATTERN.sub("[pending investigation]", text)

# Alert thresholds — module constants so they appear in a single searchable place
_CRITICAL_DEVIATION_THRESHOLD = 50.0   # % above threshold → critical_anomaly alert
_LOW_RUL_THRESHOLD_HOURS = 48.0        # hours remaining → low_rul alert

from langgraph.graph import END, START, StateGraph

from app.agents import (
    anomaly_engine,
    communication_formatter,
    diagnosis_agent,
    evidence_retrieval_agent,
    evidence_verification_agent,
    maintenance_strategy_agent,
    operational_impact_engine,
    plant_priority_engine,
    prognostics_engine,
    supervisor_planner,
)
from app.agents.base import AgentResult
from app.core.enums import MaintenancePriority, RiskLevel
from app.core.state_contract import (
    AnomalyDetail,
    DiagnosisResult,
    ExecutionPlan,
    ImpactAssessment,
    LLMTelemetry,
    ReActStep,
    RoleReports,
    RULPrediction,
    StrategyDecision,
    VerificationResult,
    VulcanOpsState,
)
from app.orchestrator.execution_trace import build_trace, now_utc, skipped_trace
from app.schemas.report import EngineerReport, ManagerReport, SupervisorReport


def _trace_node(name: str, fn):
    """Wrap a graph node so it emits structured START/END logs and optional progress updates."""
    async def wrapper(state: VulcanOpsState) -> dict[str, Any]:
        machine_name = (
            state.machine_context.machine_name
            if state.machine_context
            else str(state.active_machine_id)
        )
        _PIPELINE_LOG.info(json.dumps({
            "event": "deep_analysis",
            "machine": machine_name,
            "step": name,
            "status": "start",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))

        # Report stage progress to any active job observer.
        progress_cb = _progress_callback_ctx.get()
        if progress_cb is not None:
            try:
                await progress_cb(name)
            except Exception:
                logger.exception("Progress callback failed for stage %s", name)

        start = time.monotonic()
        try:
            result = await fn(state)
        except Exception as exc:
            _PIPELINE_LOG.error(json.dumps({
                "event": "deep_analysis",
                "machine": machine_name,
                "step": name,
                "status": "error",
                "duration_ms": round((time.monotonic() - start) * 1000, 1),
                "error": str(exc),
            }))
            raise
        _PIPELINE_LOG.info(json.dumps({
            "event": "deep_analysis",
            "machine": machine_name,
            "step": name,
            "status": "end",
            "duration_ms": round((time.monotonic() - start) * 1000, 1),
        }))
        return result
    return wrapper


def _merge_llm_telemetry(state: VulcanOpsState, raw: dict) -> LLMTelemetry:
    prev = state.llm_telemetry
    return LLMTelemetry(
        model=raw.get("model") or prev.model,
        total_input_tokens=prev.total_input_tokens,
        total_output_tokens=prev.total_output_tokens,
        total_latency_ms=prev.total_latency_ms + raw.get("latency_ms", 0.0),
        calls=prev.calls + [raw],
    )


# ── node: supervisor ──────────────────────────────────────────────────────────


async def _supervisor_node(state: VulcanOpsState) -> dict:
    """First node: decide which agents to run."""
    start = now_utc()
    try:
        result: AgentResult = await supervisor_planner.run(state)
    except Exception as exc:
        logger.exception("supervisor_planner raised unexpectedly: %s", exc)
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("supervisor_planner", start, end, result.status,
                        llm_called=True, degraded_reason=result.degraded_reason)
    if result.status == "degraded":
        logger.warning("supervisor_planner degraded: %s", result.degraded_reason)

    if result.status == "error":
        # Fall back to the full pipeline so a supervisor failure never blocks analysis.
        fallback_plan = ExecutionPlan(
            stages=[
                "anomaly", "prognostics", "evidence_retrieval", "diagnosis",
                "evidence_verification", "operational_impact", "maintenance_strategy",
                "plant_priority", "communication",
            ],
            skipped={},
            rationale="Supervisor failed — falling back to full pipeline.",
        )
        return {
            "execution_plan": fallback_plan,
            "execution_trace": [trace],
            "errors": [{"agent": "supervisor_planner", "errors": result.errors}],
        }

    plan_data = result.data.get("execution_plan") or {}
    plan = ExecutionPlan(
        stages=plan_data.get("stages", []),
        skipped=plan_data.get("skipped", {}),
        rationale=plan_data.get("rationale", ""),
    )

    return {
        "execution_plan": plan,
        "execution_trace": [trace],
    }


# ── conditional routing ───────────────────────────────────────────────────────

_ANALYTICAL_ORDER = [
    "anomaly_engine",
    "prognostics_engine",
    "evidence_retrieval_agent",
    "diagnosis_agent",
    "evidence_verification_agent",
    "operational_impact_engine",
    "maintenance_strategy_agent",
    "plant_priority_engine",
]

# Maps LangGraph node name → ExecutionPlan stage key (used by supervisor_planner).
# Stage keys ("anomaly", "prognostics", ...) are stable identifiers in the plan;
# node names may use different suffixes (_engine, _agent, _formatter, _planner).
_NODE_TO_STAGE_KEY: dict[str, str] = {
    "anomaly_engine":               "anomaly",
    "prognostics_engine":           "prognostics",
    "evidence_retrieval_agent":     "evidence_retrieval",
    "diagnosis_agent":              "diagnosis",
    "evidence_verification_agent":  "evidence_verification",
    "operational_impact_engine":    "operational_impact",
    "maintenance_strategy_agent":   "maintenance_strategy",
    "plant_priority_engine":        "plant_priority",
    "communication_formatter":      "communication",
}


def _next_active_after(state: VulcanOpsState, current: str) -> str:
    """Return the next enabled analytical stage after `current`.

    Falls through to 'communication_formatter' when no more analytical stages remain.
    Skips nodes whose invariant preconditions cannot be satisfied even if the
    supervisor plan includes them (belt-and-suspenders).
    """
    plan_stages = (
        state.execution_plan.stages if state.execution_plan else _ANALYTICAL_ORDER
    )
    try:
        start_idx = _ANALYTICAL_ORDER.index(current) + 1
    except ValueError:
        start_idx = 0

    for node_name in _ANALYTICAL_ORDER[start_idx:]:
        stage_key = _NODE_TO_STAGE_KEY.get(node_name, node_name)
        if stage_key not in plan_stages:
            continue
        if node_name == "diagnosis_agent" and state.anomaly is None:
            continue
        if node_name == "evidence_verification_agent" and state.diagnosis is None:
            continue
        return node_name

    return "communication_formatter"


# ── verification cycle routing ────────────────────────────────────────────────

_MAX_VERIFICATION_REVISIONS = 1


def _route_after_verification(state: VulcanOpsState) -> str:
    """Route forward normally, OR back to diagnosis_agent if a strong contradiction was found."""
    rec = state.verification_recommendation or "accept"
    revisions = state.verification_revision_count
    if rec == "revise_diagnosis" and revisions < _MAX_VERIFICATION_REVISIONS:
        return "diagnosis_agent"
    return _next_active_after(state, current="evidence_verification_agent")





# ── node: anomaly ─────────────────────────────────────────────────────────────


async def _anomaly_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = anomaly_engine.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("anomaly_engine", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "anomaly_engine", "errors": result.errors}],
        }

    d = result.data
    # Primary anomaly = worst-severity entry (agents already sorted by deviation)
    primary = d["anomalies"][0] if d["anomalies"] else None
    anomaly = AnomalyDetail(
        detected=d["anomaly_detected"],
        sensor=primary["sensor"] if primary else None,
        value=primary["value"] if primary else None,
        threshold=primary["threshold"] if primary else None,
        deviation_percent=primary["deviation_percent"] if primary else None,
        detected_at=datetime.fromisoformat(primary["detected_at"]) if primary else None,
    )

    # Publish critical-anomaly alert when deviation exceeds threshold
    if primary and primary.get("deviation_percent", 0) > _CRITICAL_DEVIATION_THRESHOLD:
        try:
            from app.services.alert_bus import get_alert_bus, make_critical_anomaly_alert
            machine_name = (
                state.machine_context.machine_name if state.machine_context else None
            )
            alert = make_critical_anomaly_alert(
                machine_id=str(state.active_machine_id),
                machine_name=machine_name,
                sensor=primary["sensor"],
                value=primary["value"],
                deviation_percent=primary["deviation_percent"],
            )
            get_alert_bus().publish(alert)
        except Exception as _ae:
            logger.warning("alert_bus publish (anomaly) failed: %s", _ae)

    return {
        "anomaly": anomaly,
        "execution_trace": [trace],
    }


# ── node: prognostics ─────────────────────────────────────────────────────────


async def _prognostics_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = prognostics_engine.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("prognostics_engine", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "prognostics_engine", "errors": result.errors}],
        }

    d = result.data
    hours_remaining = d.get("hours_remaining")
    rul = RULPrediction(
        remaining_useful_life_hours=hours_remaining,
        confidence=d.get("confidence"),
        basis=d.get("basis"),
    )

    # Publish low-RUL alert when estimated life drops below threshold
    if hours_remaining is not None and hours_remaining < _LOW_RUL_THRESHOLD_HOURS:
        try:
            from app.services.alert_bus import get_alert_bus, make_low_rul_alert
            machine_name = (
                state.machine_context.machine_name if state.machine_context else None
            )
            alert = make_low_rul_alert(
                machine_id=str(state.active_machine_id),
                machine_name=machine_name,
                hours_remaining=hours_remaining,
                basis=d.get("basis", "sensor extrapolation"),
            )
            get_alert_bus().publish(alert)
        except Exception as _re:
            logger.warning("alert_bus publish (rul) failed: %s", _re)

    return {
        "rul_prediction": rul,
        "execution_trace": [trace],
    }


# ── node: evidence_retrieval ──────────────────────────────────────────────────


async def _evidence_retrieval_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = await evidence_retrieval_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    d = result.data
    telem: dict = d.get("llm_telemetry") or {}
    fallback_used = bool(telem.get("fallback_used", False))
    trace = build_trace(
        "evidence_retrieval_agent", start, end, result.status,
        llm_called=not fallback_used,
    )

    _PIPELINE_LOG.info(json.dumps({
        "event":          "llm_call",
        "agent":          "evidence_retrieval_agent",
        "machine_id":     str(state.active_machine_id),
        "iterations":     telem.get("iterations", 0),
        "fallback_used":  fallback_used,
        "queries":        d.get("query_history", []),
        "chunks_found":   len(d.get("retrieved_evidence", [])),
        "status":         result.status,
    }))

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "evidence_retrieval_agent", "errors": result.errors}],
        }

    return {
        "retrieved_evidence": d.get("retrieved_evidence", []),
        "retrieval_query_history": d.get("query_history", []),
        "execution_trace": [trace],
    }


# ── node: diagnosis (LLM #1) — Invariant 1 ───────────────────────────────────


async def _diagnosis_node(state: VulcanOpsState) -> dict:
    # Invariant 1: diagnosis cannot run without anomaly data
    if state.anomaly is None:
        return {
            "execution_trace": [skipped_trace(
                "diagnosis_agent",
                "Invariant 1: state.anomaly is None — anomaly agent did not produce output",
            )],
        }

    # Detect re-pass triggered by verification cycle
    is_repass = state.verification is not None
    updates: dict[str, Any] = {}
    if is_repass:
        new_revision_count = state.verification_revision_count + 1
        updates["verification_revision_count"] = new_revision_count
        _PIPELINE_LOG.info(json.dumps({
            "event": "verification_cycle",
            "revision_count": new_revision_count,
            "contradictions_count": len(state.verification_contradictions),
            "machine_id": str(state.active_machine_id),
        }))

    # Fetch prior engineer feedback and inject into state before diagnosis
    try:
        from app.services.feedback_retrieval import get_relevant_feedback
        anomaly_sensor = state.anomaly.sensor if state.anomaly else None
        prior_fb = await get_relevant_feedback(
            machine_id=state.active_machine_id,
            failure_mode=anomaly_sensor,
            limit=5,
        )
        if prior_fb:
            updates["prior_feedback"] = prior_fb
            state = state.model_copy(update={"prior_feedback": prior_fb})
    except Exception as _fb_exc:
        logger.warning("feedback_retrieval failed (non-fatal): %s", _fb_exc)

    start = now_utc()
    try:
        result: AgentResult = await diagnosis_agent.run(state)
    except Exception as exc:
        logger.exception("diagnosis_agent raised unexpectedly: %s", exc)
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    d = result.data
    telem: dict = d.get("llm_telemetry") or {}
    cache_hit = bool(telem.get("cache_hit", False))
    trace = build_trace("diagnosis_agent", start, end, result.status,
                        llm_called=True, cache_hit=cache_hit,
                        degraded_reason=result.degraded_reason)

    _PIPELINE_LOG.info(json.dumps({
        "event":          "llm_call",
        "agent":          "diagnosis_agent",
        "machine_id":     str(state.active_machine_id),
        "model":          telem.get("model", ""),
        "latency_ms":     telem.get("latency_ms", 0.0),
        "cache_hit":      cache_hit,
        "fallback_used":  bool(telem.get("fallback_used", False)),
        "input_tokens":   telem.get("input_tokens", 0),
        "output_tokens":  telem.get("output_tokens", 0),
        "status":         result.status,
    }))

    if result.status == "degraded":
        logger.warning("diagnosis_agent degraded: %s", result.degraded_reason)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "diagnosis_agent", "errors": result.errors}],
        }

    reasoning_trace = [
        ReActStep(**step) for step in d.get("reasoning_trace", [])
    ] if d.get("reasoning_trace") else []

    diagnosis = DiagnosisResult(
        root_cause=d.get("root_cause") or "",
        failure_mode=d.get("failure_mode") or "",
        confidence=d.get("confidence", 0.5),
        supporting_evidence=d.get("evidence_used", []),
        reasoning_trace=reasoning_trace,
    )

    updates.update({
        "diagnosis": diagnosis,
        "execution_trace": [trace],
    })
    if telem:
        updates["llm_telemetry"] = _merge_llm_telemetry(state, telem)

    return updates


# ── node: evidence_verification — Invariant 2 ────────────────────────────────


async def _evidence_verification_node(state: VulcanOpsState) -> dict:
    # Invariant 2: cannot verify without a diagnosis
    if state.diagnosis is None:
        return {
            "execution_trace": [skipped_trace(
                "evidence_verification_agent",
                "Invariant 2: state.diagnosis is None — diagnosis agent did not produce output",
            )],
        }

    start = now_utc()
    try:
        result: AgentResult = await evidence_verification_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    d = result.data
    telem: dict = d.get("llm_telemetry") or {}
    cache_hit = bool(telem.get("cache_hit", False))
    trace = build_trace("evidence_verification_agent", start, end, result.status,
                        llm_called=True, cache_hit=cache_hit,
                        degraded_reason=result.degraded_reason)

    _PIPELINE_LOG.info(json.dumps({
        "event":        "llm_call",
        "agent":        "evidence_verification_agent",
        "machine_id":   str(state.active_machine_id),
        "model":        telem.get("model", ""),
        "iterations":   telem.get("iterations", 0),
        "status":       result.status,
    }))

    if result.status == "degraded":
        logger.warning("evidence_verification_agent degraded: %s", result.degraded_reason)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "evidence_verification_agent", "errors": result.errors}],
        }

    contradictions_raw: list[str] = d.get("contradictions", [])
    recommendation: str = d.get("recommendation", "accept")

    verification = VerificationResult(
        verified=d["verified"],
        verification_notes=d.get("verification_notes"),
        contradictions=contradictions_raw,
        evidence_score=d.get("evidence_score", 0.0),
        history_score=d.get("history_score", 0.0),
        combined_score=d.get("combined_score", 0.0),
    )

    # Store contradictions as dicts so diagnosis_agent can render them by key
    contradictions_dicts = [{"contradiction": c} for c in contradictions_raw]

    return {
        "verification": verification,
        "verification_contradictions": contradictions_dicts,
        "verification_recommendation": recommendation,
        "execution_trace": [trace],
    }


# ── node: operational_impact ──────────────────────────────────────────────────


async def _operational_impact_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = operational_impact_engine.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("operational_impact_engine", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "operational_impact_engine", "errors": result.errors}],
        }

    d = result.data
    impact = ImpactAssessment(
        risk_level=RiskLevel(d["risk_level"]),
        estimated_downtime_hours=d.get("estimated_downtime_hours"),
        estimated_cost_usd=d.get("estimated_cost_usd"),
        affected_production_lines=d.get("affected_production_lines", []),
        compliance_flags=d.get("compliance_flags", []),
        business_impact_summary=d.get("business_impact_summary"),
    )
    return {
        "impact": impact,
        "execution_trace": [trace],
    }


# ── node: maintenance_strategy ────────────────────────────────────────────────


async def _maintenance_strategy_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = await maintenance_strategy_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("maintenance_strategy_agent", start, end, result.status,
                        degraded_reason=result.degraded_reason)

    if result.status == "degraded":
        logger.warning("maintenance_strategy_agent degraded: %s", result.degraded_reason)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "maintenance_strategy_agent", "errors": result.errors}],
        }

    d = result.data
    strategy = StrategyDecision(
        recommended_action=d.get("immediate_action"),
        priority=MaintenancePriority(d["priority"]),
        estimated_repair_hours=d.get("estimated_repair_hours", 0.0),
        parts_required=d.get("parts_required", []),
        safety_notes=d.get("safety_notes"),
        resource_requirements=d.get("resource_requirements"),
        procurement_strategy=d.get("procurement_strategy"),
        constraint_violations=d.get("constraint_violations", []),
    )
    return {
        "strategy": strategy,
        "execution_trace": [trace],
    }


# ── node: plant_priority ──────────────────────────────────────────────────────


async def _plant_priority_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = plant_priority_engine.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("plant_priority_engine", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "plant_priority_engine", "errors": result.errors}],
        }

    d = result.data
    rank_to_priority = {
        "P1": MaintenancePriority.EMERGENCY,
        "P2": MaintenancePriority.URGENT,
        "P3": MaintenancePriority.SCHEDULED,
        "P4": MaintenancePriority.ROUTINE,
    }
    priority = rank_to_priority.get(d.get("priority_rank", "P3"), MaintenancePriority.SCHEDULED)
    return {
        "priority": priority,
        "execution_trace": [trace],
    }


# ── node: communication (LLM #2) — Invariant 3 ───────────────────────────────


async def _communication_node(state: VulcanOpsState) -> dict:
    # Invariant 3: communication requires strategy to exist
    if state.strategy is None:
        return {
            "execution_trace": [skipped_trace(
                "communication_formatter",
                "Invariant 3: state.strategy is None — cannot generate role reports without strategy",
            )],
        }

    start = now_utc()
    try:
        result: AgentResult = await communication_formatter.run(state)
    except Exception as exc:
        logger.exception("communication_formatter raised unexpectedly: %s", exc)
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    d = result.data
    telem: dict = d.get("llm_telemetry") or {}
    cache_hit = bool(telem.get("cache_hit", False))
    trace = build_trace("communication_formatter", start, end, result.status,
                        llm_called=True, cache_hit=cache_hit,
                        degraded_reason=result.degraded_reason)
    if result.status == "degraded":
        logger.warning("communication_formatter degraded: %s", result.degraded_reason)

    _PIPELINE_LOG.info(json.dumps({
        "event":          "llm_call",
        "agent":          "communication_formatter",
        "machine_id":     str(state.active_machine_id),
        "model":          telem.get("model", ""),
        "latency_ms":     telem.get("latency_ms", 0.0),
        "cache_hit":      cache_hit,
        "fallback_used":  bool(telem.get("fallback_used", False)),
        "input_tokens":   telem.get("input_tokens", 0),
        "output_tokens":  telem.get("output_tokens", 0),
        "status":         result.status,
    }))

    if result.status == "error":
        return {
            "execution_trace": [trace],
            "errors": [{"agent": "communication_formatter", "errors": result.errors}],
        }

    now = datetime.now(timezone.utc)
    machine_id = state.active_machine_id

    # Resolve required values with safe fallbacks so role reports are always constructable
    risk_level = (state.impact.risk_level if state.impact else RiskLevel.MEDIUM)
    priority = (state.strategy.priority if state.strategy else MaintenancePriority.SCHEDULED)
    root_cause = (state.diagnosis.root_cause if state.diagnosis else "Under investigation")
    recommended_action = (state.strategy.recommended_action or "See maintenance plan")
    confidence = (state.diagnosis.confidence or 0.0 if state.diagnosis else 0.0)
    repair_hours = (state.strategy.estimated_repair_hours or 0.0 if state.strategy else 0.0)
    parts = (state.strategy.parts_required if state.strategy else [])
    downtime = (state.impact.estimated_downtime_hours or 0.0 if state.impact else 0.0)
    prod_lines = (state.impact.affected_production_lines if state.impact else [])
    resources = (state.strategy.resource_requirements or "")
    cost = (state.impact.estimated_cost_usd or 0.0 if state.impact else 0.0)
    compliance = (state.impact.compliance_flags if state.impact else [])
    biz_impact = (state.impact.business_impact_summary or "")

    role_reports = RoleReports(
        engineer=EngineerReport(
            report_id=uuid.uuid4(),
            machine_id=machine_id,
            generated_at=now,
            root_cause=root_cause,
            recommended_action=recommended_action,
            risk_level=risk_level,
            confidence=confidence,
            priority=priority,
            estimated_repair_hours=repair_hours,
            parts_required=parts,
            safety_notes=d.get("engineer_summary", ""),
        ),
        supervisor=SupervisorReport(
            report_id=uuid.uuid4(),
            machine_id=machine_id,
            generated_at=now,
            risk_level=risk_level,
            priority=priority,
            recommended_action=recommended_action,
            estimated_downtime_hours=downtime,
            affected_production_lines=prod_lines,
            resource_requirements=d.get("supervisor_summary", resources),
        ),
        manager=ManagerReport(
            report_id=uuid.uuid4(),
            machine_id=machine_id,
            generated_at=now,
            risk_level=risk_level,
            root_cause=root_cause,
            business_impact=d.get("manager_summary", biz_impact),
            estimated_cost_usd=cost,
            recommended_action=recommended_action,
            compliance_flags=compliance,
        ),
    )

    updates: dict[str, Any] = {
        "role_reports": role_reports,
        "execution_trace": [trace],
    }
    if telem := d.get("llm_telemetry"):
        updates["llm_telemetry"] = _merge_llm_telemetry(state, telem)

    return updates


# ── report quality matrix ─────────────────────────────────────────────────────

class ReportQuality:
    """Evidence- and confidence-aware classification for final report handling."""

    # Evidence is considered weak if both documentary and historical corroboration
    # are below this threshold. The verification agent uses 0.25; we align with it.
    EVIDENCE_WEAK = 0.25

    # Confidence bands. These are NOT hard cutoffs for suppressing content;
    # they inform how aggressively we sanitize and what fallback prose to use.
    LOW_CONFIDENCE = 0.50
    MODERATE_CONFIDENCE = 0.70
    HIGH_CONFIDENCE = 0.85


class ReportDisposition:
    SPECIFIC = "specific"
    CAUTIOUS = "cautious"
    FALLBACK = "fallback"


def _classify_report(
    confidence: float,
    verified: bool | None,
    evidence_score: float,
    history_score: float,
    has_evidence: bool,
) -> tuple[str, str]:
    """Return (disposition, reason) for the final report.

    Matrix:
      - strong evidence + high confidence   → specific diagnosis and action
      - moderate evidence / moderate conf   → cautious diagnosis with partial recs
      - weak evidence + low confidence      → manual-inspection fallback
      - circuit breaker / API failure       → safe fallback (handled upstream)
    """
    if confidence >= ReportQuality.HIGH_CONFIDENCE and verified:
        return ReportDisposition.SPECIFIC, "high_confidence_verified"
    if confidence >= ReportQuality.MODERATE_CONFIDENCE and (verified or evidence_score >= ReportQuality.EVIDENCE_WEAK):
        return ReportDisposition.SPECIFIC, "moderate_confidence_supported"
    if confidence >= ReportQuality.LOW_CONFIDENCE and (verified or evidence_score >= ReportQuality.EVIDENCE_WEAK or history_score >= ReportQuality.EVIDENCE_WEAK):
        return ReportDisposition.CAUTIOUS, "low_confidence_partial_evidence"
    if has_evidence:
        return ReportDisposition.CAUTIOUS, "evidence_present_but_weak"
    return ReportDisposition.FALLBACK, "insufficient_evidence"


# ── explainability & procurement gap helpers ──────────────────────────────────


def _build_evidence_chain(state: VulcanOpsState) -> list[dict[str, Any]]:
    """Construct a human-readable evidence chain from existing agent outputs.

    Uses only fields already populated by anomaly, evidence_retrieval, and
    maintenance_history agents — no new agents required.
    """
    chain: list[dict[str, Any]] = []
    step = 1

    # 1. Sensor evidence from anomaly agent
    if state.anomaly and state.anomaly.detected:
        sensor = state.anomaly.sensor or "sensor"
        value = state.anomaly.value
        threshold = state.anomaly.threshold
        deviation = state.anomaly.deviation_percent
        pieces: list[str] = []
        if value is not None:
            pieces.append(f"{sensor}={value:.2f}")
        if threshold is not None:
            pieces.append(f"threshold={threshold:.2f}")
        if deviation is not None:
            pieces.append(f"deviation={deviation:.1f}%")
        evidence = "; ".join(pieces) if pieces else f"{sensor} anomaly detected"
        chain.append({
            "step": step,
            "type": "sensor",
            "source": "sensor_readings",
            "evidence": evidence,
        })
        step += 1
    elif state.anomaly:
        chain.append({
            "step": step,
            "type": "sensor",
            "source": "sensor_readings",
            "evidence": "No anomaly detected in current sensor window",
        })
        step += 1

    # 2. Historical evidence from maintenance records
    if state.maintenance_history:
        latest = state.maintenance_history[0]
        evidence = (
            f"{latest.action_taken or 'Maintenance action'} "
            f"for {latest.failure_mode or 'failure mode'} "
            f"on {latest.date.isoformat() if latest.date else 'recorded date'}"
        )
        chain.append({
            "step": step,
            "type": "history",
            "source": "maintenance_history",
            "evidence": evidence,
        })
        step += 1

    # 3. Manual / documentary evidence from evidence_retrieval agent
    if state.retrieved_evidence:
        top = state.retrieved_evidence[0]
        source = top.get("source", "equipment_manual")
        chunk = top.get("chunk", "")
        # Truncate very long chunks for readability while keeping meaning.
        evidence = chunk[:280] + "…" if len(chunk) > 280 else chunk
        chain.append({
            "step": step,
            "type": "manual",
            "source": source,
            "evidence": evidence,
        })

    return chain


def _compute_explainability_score(evidence_chain: list[dict[str, Any]]) -> int:
    """0-100 score reflecting how well the diagnosis is grounded across sources."""
    type_counts: dict[str, int] = {}
    for item in evidence_chain:
        type_counts[item.get("type", "")] = type_counts.get(item.get("type", ""), 0) + 1

    # Cap each source category at 1 so the score maxes at 100 when all three
    # source types are present.
    sensor = min(type_counts.get("sensor", 0), 1)
    history = min(type_counts.get("history", 0), 1)
    manual = min(type_counts.get("manual", 0), 1)

    raw = sensor * 0.4 + history * 0.3 + manual * 0.3
    return min(int(raw * 100), 100)


def _compute_procurement_gap(state: VulcanOpsState) -> dict[str, Any]:
    """Build procurement gap summary from maintenance_strategy_agent's constraint_violations.

    The agent queries live inventory and populates constraint_violations and
    procurement_strategy directly; this function surfaces those for final_report_json.
    """
    violations = state.strategy.constraint_violations if state.strategy else []
    strategy_text = state.strategy.procurement_strategy if state.strategy else None

    return {
        "procurement_gap": len(violations) > 0,
        "constraint_violations": violations,
        "procurement_strategy": strategy_text,
    }


# ── node: finalize — Invariants 4 & 5 ────────────────────────────────────────


async def _finalize_node(state: VulcanOpsState) -> dict:
    # Only track errors this node itself generates — pre-existing state.errors are
    # already accumulated via the `add` reducer and must not be re-emitted here.
    new_errors: list[dict[str, Any]] = []
    start = now_utc()

    # Invariant 4: if sensor data was provided, diagnosis must not be empty
    if state.sensor_readings and (
        state.diagnosis is None or not state.diagnosis.root_cause
    ):
        new_errors.append({
            "agent": "finalize",
            "errors": [
                "Invariant 4: sensor readings exist but diagnosis produced no root_cause. "
                "Investigation is incomplete."
            ],
        })

    # Invariant 5: final report cannot be built without a root cause
    if state.diagnosis is None or not state.diagnosis.root_cause:
        logger.warning(
            "Invariant 5: finalize blocked — diagnosis=%r new_errors=%r",
            state.diagnosis, new_errors,
        )
        trace = build_trace("finalize", start, now_utc(), "error")

        # Build stub role reports so StoredRoleReport is never empty after a
        # completed deep-analysis job, even when the pipeline failed early.
        _SAFE_ACTION = (
            "Perform manual inspection and validate sensor readings "
            "before executing repair procedures."
        )
        _SAFE_CONTENT = (
            "Evidence is insufficient to determine root cause. "
            "Perform manual inspection before repair actions."
        )
        _now_rr = datetime.now(timezone.utc)
        _mid = state.active_machine_id
        _rl = state.impact.risk_level if state.impact else RiskLevel.MEDIUM

        stub_diagnosis = DiagnosisResult(
            root_cause="manual inspection required",
            failure_mode="insufficient evidence",
            confidence=0.0,
        )
        stub_reports = RoleReports(
            engineer=EngineerReport(
                report_id=uuid.uuid4(), machine_id=_mid, generated_at=_now_rr,
                root_cause="manual inspection required",
                recommended_action=_SAFE_ACTION,
                risk_level=_rl, confidence=0.0,
                priority=MaintenancePriority.URGENT,
                estimated_repair_hours=0.0, parts_required=[],
                safety_notes=_SAFE_CONTENT,
            ),
            supervisor=SupervisorReport(
                report_id=uuid.uuid4(), machine_id=_mid, generated_at=_now_rr,
                risk_level=_rl, priority=MaintenancePriority.URGENT,
                recommended_action=_SAFE_ACTION,
                estimated_downtime_hours=0.0, affected_production_lines=[],
                resource_requirements=(
                    "Pending manual inspection. Do not allocate repair resources "
                    "until diagnosis is confirmed."
                ),
            ),
            manager=ManagerReport(
                report_id=uuid.uuid4(), machine_id=_mid, generated_at=_now_rr,
                risk_level=_rl, root_cause="manual inspection required",
                business_impact=(
                    "Analysis incomplete — manual inspection required before "
                    "business impact can be quantified."
                ),
                estimated_cost_usd=0.0, recommended_action=_SAFE_ACTION,
                compliance_flags=[],
            ),
        )
        # Prefer role reports already produced by communication_formatter (e.g. degraded
        # fallback text) over the generic stub — only use stub if none exist yet.
        final_role_reports = state.role_reports if state.role_reports is not None else stub_reports
        return {
            "execution_trace": [trace],
            "errors": new_errors,
            "diagnosis": stub_diagnosis,
            "role_reports": final_role_reports,
        }

    # ── EVIDENCE- AND CONFIDENCE-AWARE FINALIZATION ───────────────────────────
    # Use the report-quality matrix to decide how much to sanitize vs. preserve.
    # Verified diagnoses with moderate-or-better confidence keep their concrete
    # content; weak/unverified cases get cautious or fallback treatment.
    _confidence = state.diagnosis.confidence or 0.0
    _root_cause = state.diagnosis.root_cause or ""
    _failure_mode = state.diagnosis.failure_mode or ""

    verified = state.verification.verified if state.verification else False
    evidence_score = state.verification and getattr(state.verification, "evidence_score", 0.0) or 0.0
    history_score = state.verification and getattr(state.verification, "history_score", 0.0) or 0.0
    has_evidence = bool(state.retrieved_evidence)

    disposition, fallback_reason = _classify_report(
        confidence=_confidence,
        verified=verified,
        evidence_score=evidence_score,
        history_score=history_score,
        has_evidence=has_evidence,
    )

    pending: dict[str, Any] = {}
    evidence_chain = _build_evidence_chain(state)
    explainability_score = _compute_explainability_score(evidence_chain)
    procurement_gap = _compute_procurement_gap(state)

    _telemetry: dict[str, Any] = {
        "evidence_score": evidence_score,
        "history_score": history_score,
        "diagnosis_confidence": _confidence,
        "verified": verified,
        "fallback_used": False,
        "uncertainty_reason": None,
        "circuit_breaker_state": "CLOSED",
        "final_report_status": disposition,
        "evidence_chain": evidence_chain,
        "explainability_score": explainability_score,
        "procurement_gap": procurement_gap,
    }

    # Track whether the LLM already emitted a fallback diagnosis
    is_llm_fallback = (
        _root_cause == "manual inspection required"
        or _failure_mode == "insufficient evidence"
    )

    if disposition == ReportDisposition.FALLBACK or is_llm_fallback:
        _SAFE_ACTION = (
            "Perform manual inspection and validate sensor readings "
            "before executing repair procedures."
        )
        _SAFE_ROOT_CAUSE = (
            "Evidence insufficient to determine root cause. "
            "Perform manual inspection before repair actions."
        )

        _telemetry["fallback_used"] = True
        _telemetry["uncertainty_reason"] = fallback_reason if not is_llm_fallback else "llm_insufficient_evidence"

        # Only overwrite the diagnosis if the LLM itself did not already ask for inspection
        if state.diagnosis and not is_llm_fallback:
            pending["diagnosis"] = state.diagnosis.model_copy(update={
                "root_cause": _SAFE_ROOT_CAUSE,
                "failure_mode": "insufficient evidence",
            })

        # Override strategy — recommended_action and priority
        if state.strategy:
            pending["strategy"] = state.strategy.model_copy(update={
                "recommended_action": _SAFE_ACTION,
                "priority": MaintenancePriority.URGENT,
            })

        # Override verification — keep raw notes but mark verified False for safety
        if state.verification:
            pending["verification"] = state.verification.model_copy(update={
                "verified": False,
            })

        # Override role report text fields after communication agent has set them.
        # If communication was skipped (strategy was None), create stub reports so
        # StoredRoleReport is never stored with empty content.
        rr = state.role_reports
        _rl_fb = state.impact.risk_level if state.impact else RiskLevel.MEDIUM
        _now_fb = datetime.now(timezone.utc)
        _mid_fb = state.active_machine_id
        _conf_fb = _confidence

        if rr.engineer is None:
            rr = RoleReports(
                engineer=EngineerReport(
                    report_id=uuid.uuid4(), machine_id=_mid_fb, generated_at=_now_fb,
                    root_cause="manual inspection required",
                    recommended_action=_SAFE_ACTION,
                    risk_level=_rl_fb, confidence=_conf_fb,
                    priority=MaintenancePriority.URGENT,
                    estimated_repair_hours=0.0, parts_required=[],
                    safety_notes=(
                        "Evidence is insufficient to determine root cause. "
                        "Perform manual inspection before repair actions."
                    ),
                ),
                supervisor=SupervisorReport(
                    report_id=uuid.uuid4(), machine_id=_mid_fb, generated_at=_now_fb,
                    risk_level=_rl_fb, priority=MaintenancePriority.URGENT,
                    recommended_action=_SAFE_ACTION,
                    estimated_downtime_hours=0.0, affected_production_lines=[],
                    resource_requirements=(
                        "Verification: Pending. Escalate for manual inspection. "
                        "Do not allocate major repair resources until diagnosis is confirmed."
                    ),
                ),
                manager=ManagerReport(
                    report_id=uuid.uuid4(), machine_id=_mid_fb, generated_at=_now_fb,
                    risk_level=_rl_fb, root_cause="manual inspection required",
                    business_impact=(
                        "Verification: Preliminary assessment only. "
                        "Business impact estimates are provisional until inspection "
                        "confirms the root cause."
                    ),
                    estimated_cost_usd=0.0, recommended_action=_SAFE_ACTION,
                    compliance_flags=[],
                ),
            )
        else:
            # Preserve degraded communication_formatter output — it already contains
            # structured raw-field info that is more useful than the generic stub.
            _comm_degraded = (rr.engineer.safety_notes or "").startswith(
                "[Communication formatter unavailable"
            )
            if not _comm_degraded:
                rr.engineer.safety_notes = (
                    "Evidence is insufficient to determine root cause. "
                    "Perform manual inspection before repair actions."
                )
            rr.engineer.recommended_action = "Manual inspection before repair."
            rr.engineer.priority = MaintenancePriority.URGENT
            if rr.supervisor and not _comm_degraded:
                rr.supervisor.resource_requirements = _sanitize_uncertain_text(
                    "Verification: Pending. "
                    "Escalate for manual inspection. Do not allocate major repair "
                    "resources until diagnosis is confirmed."
                )
                rr.supervisor.recommended_action = _SAFE_ACTION
                rr.supervisor.priority = MaintenancePriority.URGENT
            if rr.manager and not _comm_degraded:
                rr.manager.business_impact = _sanitize_uncertain_text(
                    "Verification: Preliminary assessment only. "
                    "Business impact estimates are provisional until inspection "
                    "confirms the root cause."
                )
                rr.manager.recommended_action = _SAFE_ACTION
        pending["role_reports"] = rr

    elif disposition == ReportDisposition.CAUTIOUS:
        _telemetry["uncertainty_reason"] = fallback_reason
        # Preserve diagnosis and strategy, but sanitize any absolute certainty claims
        # in role reports while keeping concrete component names and actions.
        rr = state.role_reports
        if rr.engineer and rr.engineer.safety_notes:
            rr.engineer.safety_notes = _sanitize_uncertain_text(rr.engineer.safety_notes)
        if rr.supervisor and rr.supervisor.resource_requirements:
            rr.supervisor.resource_requirements = _sanitize_uncertain_text(
                rr.supervisor.resource_requirements
            )
        if rr.manager and rr.manager.business_impact:
            rr.manager.business_impact = _sanitize_uncertain_text(rr.manager.business_impact)
        pending["role_reports"] = rr

    else:
        # SPECIFIC: preserve everything as-is
        _telemetry["uncertainty_reason"] = None

    # Resolve effective strategy/verification (overridden if uncertain)
    eff_strategy = pending.get("strategy", state.strategy)
    eff_verification = pending.get("verification", state.verification)

    eff_diagnosis = pending.get("diagnosis", state.diagnosis)
    now = datetime.now(timezone.utc)
    final_report: dict[str, Any] = {
        "report_id": str(uuid.uuid4()),
        "machine_id": str(state.active_machine_id),
        "generated_at": now.isoformat(),
        "root_cause": eff_diagnosis.root_cause if eff_diagnosis else None,
        "failure_mode": eff_diagnosis.failure_mode if eff_diagnosis else None,
        "diagnosis_confidence": eff_diagnosis.confidence if eff_diagnosis else None,
        "risk_level": state.impact.risk_level.value if state.impact and state.impact.risk_level else None,
        "estimated_downtime_hours": state.impact.estimated_downtime_hours if state.impact else None,
        "estimated_cost_usd": state.impact.estimated_cost_usd if state.impact else None,
        "recommended_action": eff_strategy.recommended_action if eff_strategy else None,
        "priority": state.priority.value if state.priority else None,
        "verification_passed": eff_verification.verified if eff_verification else None,
        "rul_hours": state.rul_prediction.remaining_useful_life_hours if state.rul_prediction else None,
        "role_reports_generated": {
            "engineer":   state.role_reports.engineer is not None,
            "supervisor": state.role_reports.supervisor is not None,
            "manager":    state.role_reports.manager is not None,
        },
        "verification_contradictions": state.verification_contradictions,
        "verification_revision_count": state.verification_revision_count,
        "pipeline_errors": len(state.errors) + len(new_errors),
        # Report-quality telemetry
        "evidence_score": _telemetry.get("evidence_score"),
        "history_score": _telemetry.get("history_score"),
        "fallback_used": _telemetry.get("fallback_used"),
        "uncertainty_reason": _telemetry.get("uncertainty_reason"),
        "circuit_breaker_state": _telemetry.get("circuit_breaker_state"),
        "final_report_status": _telemetry.get("final_report_status"),
        # Explainability & procurement gap intelligence
        "evidence_chain": _telemetry.get("evidence_chain"),
        "explainability_score": _telemetry.get("explainability_score"),
        "procurement_gap": _telemetry.get("procurement_gap"),
    }

    trace = build_trace("finalize", start, now_utc(), "success")

    # Publish high-risk alert for supervisor + manager when risk is high/critical
    _rl_value = final_report.get("risk_level")
    if _rl_value in {"high", "critical"}:
        try:
            from app.services.alert_bus import get_alert_bus, make_high_risk_job_alert
            _machine_name = (
                state.machine_context.machine_name if state.machine_context else None
            )
            _batch_id = final_report.get("report_id")
            _alert = make_high_risk_job_alert(
                machine_id=str(state.active_machine_id),
                machine_name=_machine_name,
                risk_level=_rl_value,
                root_cause=final_report.get("root_cause") or "unknown",
                recommended_action=final_report.get("recommended_action") or "",
                report_batch_id=_batch_id,
            )
            get_alert_bus().publish(_alert)
        except Exception as _fe:
            logger.warning("alert_bus publish (high_risk) failed: %s", _fe)

    return {
        "final_report": final_report,
        "execution_trace": [trace],
        "errors": new_errors,
        **pending,   # strategy, verification, role_reports if uncertain
    }


# ── graph construction ────────────────────────────────────────────────────────


def build_graph() -> Any:
    """
    Build and compile the VulcanOps agent graph.
    Returns a compiled LangGraph runnable.
    Intended to be called once at startup and reused.
    """
    graph = StateGraph(VulcanOpsState)

    graph.add_node("supervisor_planner",            _trace_node("supervisor_planner", _supervisor_node))
    graph.add_node("anomaly_engine",                _trace_node("anomaly_engine", _anomaly_node))
    graph.add_node("prognostics_engine",            _trace_node("prognostics_engine", _prognostics_node))
    graph.add_node("evidence_retrieval_agent",      _trace_node("evidence_retrieval_agent", _evidence_retrieval_node))
    graph.add_node("diagnosis_agent",               _trace_node("diagnosis_agent", _diagnosis_node))
    graph.add_node("evidence_verification_agent",   _trace_node("evidence_verification_agent", _evidence_verification_node))
    graph.add_node("operational_impact_engine",     _trace_node("operational_impact_engine", _operational_impact_node))
    graph.add_node("maintenance_strategy_agent",    _trace_node("maintenance_strategy_agent", _maintenance_strategy_node))
    graph.add_node("plant_priority_engine",         _trace_node("plant_priority_engine", _plant_priority_node))
    graph.add_node("communication_formatter",       _trace_node("communication_formatter", _communication_node))
    graph.add_node("finalize_report",               _trace_node("finalize_report", _finalize_node))

    graph.add_edge(START, "supervisor_planner")

    # Supervisor fans out to first active analytical stage (or straight to
    # communication_formatter if the plan skips all analytical stages).
    graph.add_conditional_edges(
        "supervisor_planner",
        lambda s: _next_active_after(s, current="supervisor_planner"),
        {n: n for n in _ANALYTICAL_ORDER + ["communication_formatter"]},
    )

    # Each analytical stage fans forward to the next active stage.
    # evidence_verification_agent is handled separately (back-edge to diagnosis_agent).
    for _i, _node in enumerate(_ANALYTICAL_ORDER):
        if _node == "evidence_verification_agent":
            continue
        _forward_targets = {n: n for n in _ANALYTICAL_ORDER[_i + 1:] + ["communication_formatter"]}
        graph.add_conditional_edges(
            _node,
            lambda s, _n=_node: _next_active_after(s, current=_n),
            _forward_targets,
        )

    # evidence_verification_agent: either advances forward OR cycles back to diagnosis_agent.
    _ev_idx = _ANALYTICAL_ORDER.index("evidence_verification_agent")
    _ev_forward = {n: n for n in _ANALYTICAL_ORDER[_ev_idx + 1:] + ["communication_formatter"]}
    _ev_targets = {"diagnosis_agent": "diagnosis_agent", **_ev_forward}
    graph.add_conditional_edges(
        "evidence_verification_agent",
        _route_after_verification,
        _ev_targets,
    )

    graph.add_edge("communication_formatter", "finalize_report")
    graph.add_edge("finalize_report", END)

    return graph.compile()


# Module-level singleton — compiled once, reused across requests
_compiled_graph = None


def get_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
