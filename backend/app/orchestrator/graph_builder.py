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

from langgraph.graph import END, START, StateGraph

from app.agents import (
    anomaly_agent,
    communication_agent,
    diagnosis_agent,
    evidence_retrieval_agent,
    evidence_verification_agent,
    maintenance_strategy_agent,
    operational_impact_agent,
    plant_priority_agent,
    prognostics_agent,
)
from app.agents.base import AgentResult
from app.core.enums import MaintenancePriority, RiskLevel
from app.core.state_contract import (
    AnomalyDetail,
    DiagnosisResult,
    ImpactAssessment,
    LLMTelemetry,
    RoleReports,
    RULPrediction,
    StrategyDecision,
    VerificationResult,
    VulcanOpsState,
)
from app.orchestrator.execution_trace import build_trace, now_utc, skipped_trace
from app.schemas.report import EngineerReport, ManagerReport, SupervisorReport

# ── helpers ───────────────────────────────────────────────────────────────────


def _append_trace(state: VulcanOpsState, trace: dict) -> list[dict[str, Any]]:
    return state.execution_trace + [trace]


def _append_error(state: VulcanOpsState, agent: str, errors: list[str]) -> list[dict[str, Any]]:
    return state.errors + [{"agent": agent, "errors": errors}]


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


# ── node: anomaly ─────────────────────────────────────────────────────────────


async def _anomaly_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = anomaly_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("anomaly_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "anomaly_agent", result.errors),
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
    return {
        "anomaly": anomaly,
        "execution_trace": _append_trace(state, trace),
    }


# ── node: prognostics ─────────────────────────────────────────────────────────


async def _prognostics_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = prognostics_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("prognostics_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "prognostics_agent", result.errors),
        }

    d = result.data
    rul = RULPrediction(
        remaining_useful_life_hours=d.get("hours_remaining"),
        confidence=d.get("confidence"),
        basis=d.get("basis"),
    )
    return {
        "rul_prediction": rul,
        "execution_trace": _append_trace(state, trace),
    }


# ── node: evidence_retrieval ──────────────────────────────────────────────────


async def _evidence_retrieval_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = await evidence_retrieval_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("evidence_retrieval_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "evidence_retrieval_agent", result.errors),
        }

    return {
        "retrieved_evidence": result.data.get("retrieved_evidence", []),
        "execution_trace": _append_trace(state, trace),
    }


# ── node: diagnosis (LLM #1) — Invariant 1 ───────────────────────────────────


async def _diagnosis_node(state: VulcanOpsState) -> dict:
    # Invariant 1: diagnosis cannot run without anomaly data
    if state.anomaly is None:
        return {
            "execution_trace": _append_trace(
                state,
                skipped_trace(
                    "diagnosis_agent",
                    "Invariant 1: state.anomaly is None — anomaly agent did not produce output",
                ),
            ),
        }

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
                        llm_called=True, cache_hit=cache_hit)

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

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "diagnosis_agent", result.errors),
        }

    diagnosis = DiagnosisResult(
        root_cause=d.get("root_cause") or "",
        failure_mode=d.get("failure_mode") or "",
        confidence=d.get("confidence", 0.5),
        supporting_evidence=d.get("evidence_used", []),
    )

    updates: dict[str, Any] = {
        "diagnosis": diagnosis,
        "execution_trace": _append_trace(state, trace),
    }
    if telem:
        updates["llm_telemetry"] = _merge_llm_telemetry(state, telem)

    return updates


# ── node: evidence_verification — Invariant 2 ────────────────────────────────


async def _evidence_verification_node(state: VulcanOpsState) -> dict:
    # Invariant 2: cannot verify without a diagnosis
    if state.diagnosis is None:
        return {
            "execution_trace": _append_trace(
                state,
                skipped_trace(
                    "evidence_verification_agent",
                    "Invariant 2: state.diagnosis is None — diagnosis agent did not produce output",
                ),
            ),
        }

    start = now_utc()
    try:
        result: AgentResult = evidence_verification_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("evidence_verification_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "evidence_verification_agent", result.errors),
        }

    d = result.data
    verification = VerificationResult(
        verified=d["verified"],
        verification_notes=d.get("verification_notes"),
        contradictions=d.get("warnings", []),
        evidence_score=d.get("evidence_score", 0.0),
        history_score=d.get("history_score", 0.0),
        combined_score=d.get("combined_score", 0.0),
    )
    return {
        "verification": verification,
        "execution_trace": _append_trace(state, trace),
    }


# ── node: operational_impact ──────────────────────────────────────────────────


async def _operational_impact_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = operational_impact_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("operational_impact_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "operational_impact_agent", result.errors),
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
        "execution_trace": _append_trace(state, trace),
    }


# ── node: maintenance_strategy ────────────────────────────────────────────────


async def _maintenance_strategy_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = maintenance_strategy_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("maintenance_strategy_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "maintenance_strategy_agent", result.errors),
        }

    d = result.data
    strategy = StrategyDecision(
        recommended_action=d.get("immediate_action"),
        priority=MaintenancePriority(d["priority"]),
        estimated_repair_hours=d.get("estimated_repair_hours", 0.0),
        parts_required=d.get("parts_required", []),
        safety_notes=d.get("safety_notes"),
        resource_requirements=d.get("resource_requirements"),
    )
    return {
        "strategy": strategy,
        "execution_trace": _append_trace(state, trace),
    }


# ── node: plant_priority ──────────────────────────────────────────────────────


async def _plant_priority_node(state: VulcanOpsState) -> dict:
    start = now_utc()
    try:
        result: AgentResult = plant_priority_agent.run(state)
    except Exception as exc:
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    trace = build_trace("plant_priority_agent", start, end, result.status)

    if result.status == "error":
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "plant_priority_agent", result.errors),
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
        "execution_trace": _append_trace(state, trace),
    }


# ── node: communication (LLM #2) — Invariant 3 ───────────────────────────────


async def _communication_node(state: VulcanOpsState) -> dict:
    # Invariant 3: communication requires strategy to exist
    if state.strategy is None:
        return {
            "execution_trace": _append_trace(
                state,
                skipped_trace(
                    "communication_agent",
                    "Invariant 3: state.strategy is None — cannot generate role reports without strategy",
                ),
            ),
        }

    start = now_utc()
    try:
        result: AgentResult = await communication_agent.run(state)
    except Exception as exc:
        logger.exception("communication_agent raised unexpectedly: %s", exc)
        result = AgentResult(status="error", data={}, errors=[str(exc)])
    end = now_utc()

    d = result.data
    telem: dict = d.get("llm_telemetry") or {}
    cache_hit = bool(telem.get("cache_hit", False))
    trace = build_trace("communication_agent", start, end, result.status,
                        llm_called=True, cache_hit=cache_hit)

    _PIPELINE_LOG.info(json.dumps({
        "event":          "llm_call",
        "agent":          "communication_agent",
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
            "execution_trace": _append_trace(state, trace),
            "errors": _append_error(state, "communication_agent", result.errors),
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
        "execution_trace": _append_trace(state, trace),
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

# Part → typical procurement lead time in days.
# Keys are written as human-readable phrases; matching normalises underscores
# so "thermal gasket" matches both key and free-text part descriptions.
_PROCUREMENT_LEAD_TIMES = {
    "bearing": 21,
    "coupling": 14,
    "lubricant": 3,
    "oil": 3,
    "thermal gasket": 10,
}


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


def _match_parts_to_lead_times(
    parts_required: list[str],
    root_cause: str | None = None,
    failure_mode: str | None = None,
) -> dict[str, int]:
    """Map free-text parts to known lead-time categories (case-insensitive).

    Searches both the strategy parts list and the diagnosis text so gaps are
    detected even when the parts list contains only a generic placeholder.
    """
    matched: dict[str, int] = {}
    diagnosis_text = " ".join(filter(None, [root_cause, failure_mode])).lower()

    for part in parts_required:
        part_lower = part.lower()
        for key, lead in _PROCUREMENT_LEAD_TIMES.items():
            key_lower = key.lower()
            if key_lower in part_lower or key_lower.replace(" ", "_") in part_lower:
                matched[part] = lead
                break

    # Also scan root_cause / failure_mode for part keywords not already present
    # in the parts list. This keeps the gap grounded in the diagnosis itself.
    for key, lead in _PROCUREMENT_LEAD_TIMES.items():
        key_lower = key.lower()
        if key_lower in diagnosis_text or key_lower.replace(" ", "_") in diagnosis_text:
            if key not in matched:
                matched[key] = lead

    return matched


def _compute_procurement_gap(state: VulcanOpsState) -> dict[str, Any]:
    """Detect parts whose lead time exceeds the predicted RUL.

    Returns a dict suitable for embedding in final_report_json.
    """
    rul_hours = state.rul_prediction.remaining_useful_life_hours if state.rul_prediction else None
    parts_required = state.strategy.parts_required if state.strategy else []
    root_cause = state.diagnosis.root_cause if state.diagnosis else None
    failure_mode = state.diagnosis.failure_mode if state.diagnosis else None

    result: dict[str, Any] = {
        "procurement_gap": False,
        "rul_days": None,
        "at_risk_parts": [],
    }

    if rul_hours is None:
        return result

    rul_days = rul_hours / 24.0
    result["rul_days"] = round(rul_days, 1)

    matched = _match_parts_to_lead_times(parts_required, root_cause, failure_mode)
    at_risk: list[dict[str, Any]] = []
    for part, lead_days in matched.items():
        if rul_days < lead_days:
            at_risk.append({
                "part": part,
                "lead_time_days": lead_days,
                "rul_days": round(rul_days, 1),
                "gap_days": round(lead_days - rul_days, 1),
            })

    if at_risk:
        result["procurement_gap"] = True
        result["at_risk_parts"] = at_risk
        first = at_risk[0]
        result["recommended_action"] = (
            f"Expedite {first['part']} procurement. "
            f"Current lead time ({first['lead_time_days']} days) exceeds predicted remaining life ({first['rul_days']} days)."
        )

    return result


# ── node: finalize — Invariants 4 & 5 ────────────────────────────────────────


async def _finalize_node(state: VulcanOpsState) -> dict:
    errors: list[dict[str, Any]] = list(state.errors)
    start = now_utc()

    # Invariant 4: if sensor data was provided, diagnosis must not be empty
    if state.sensor_readings and (
        state.diagnosis is None or not state.diagnosis.root_cause
    ):
        errors.append({
            "agent": "finalize",
            "errors": [
                "Invariant 4: sensor readings exist but diagnosis produced no root_cause. "
                "Investigation is incomplete."
            ],
        })

    # Invariant 5: final report cannot be built without a root cause
    if state.diagnosis is None or not state.diagnosis.root_cause:
        logger.warning(
            "Invariant 5: finalize blocked — diagnosis=%r errors=%r",
            state.diagnosis, errors,
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
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": errors,
            "diagnosis": stub_diagnosis,
            "role_reports": stub_reports,
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
            state.diagnosis.root_cause = _SAFE_ROOT_CAUSE
            state.diagnosis.failure_mode = "insufficient evidence"

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
            rr.engineer.safety_notes = (
                "Evidence is insufficient to determine root cause. "
                "Perform manual inspection before repair actions."
            )
            rr.engineer.recommended_action = "Manual inspection before repair."
            rr.engineer.priority = MaintenancePriority.URGENT
            if rr.supervisor:
                rr.supervisor.resource_requirements = _sanitize_uncertain_text(
                    "Verification: Pending. "
                    "Escalate for manual inspection. Do not allocate major repair "
                    "resources until diagnosis is confirmed."
                )
                rr.supervisor.recommended_action = _SAFE_ACTION
                rr.supervisor.priority = MaintenancePriority.URGENT
            if rr.manager:
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

    now = datetime.now(timezone.utc)
    final_report: dict[str, Any] = {
        "report_id": str(uuid.uuid4()),
        "machine_id": str(state.active_machine_id),
        "generated_at": now.isoformat(),
        "root_cause": state.diagnosis.root_cause,
        "failure_mode": state.diagnosis.failure_mode,
        "diagnosis_confidence": state.diagnosis.confidence,
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
        "pipeline_errors": len(errors),
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
    return {
        "final_report": final_report,
        "execution_trace": _append_trace(state, trace),
        "errors": errors,
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

    graph.add_node("anomaly_agent",                 _trace_node("anomaly_agent", _anomaly_node))
    graph.add_node("prognostics_agent",             _trace_node("prognostics_agent", _prognostics_node))
    graph.add_node("evidence_retrieval_agent",      _trace_node("evidence_retrieval_agent", _evidence_retrieval_node))
    graph.add_node("diagnosis_agent",               _trace_node("diagnosis_agent", _diagnosis_node))
    graph.add_node("evidence_verification_agent",   _trace_node("evidence_verification_agent", _evidence_verification_node))
    graph.add_node("operational_impact_agent",      _trace_node("operational_impact_agent", _operational_impact_node))
    graph.add_node("maintenance_strategy_agent",    _trace_node("maintenance_strategy_agent", _maintenance_strategy_node))
    graph.add_node("plant_priority_agent",          _trace_node("priority_engine", _plant_priority_node))
    graph.add_node("communication_agent",           _trace_node("communication_agent", _communication_node))
    graph.add_node("finalize_report",               _trace_node("finalize_report", _finalize_node))

    graph.add_edge(START,                            "anomaly_agent")
    graph.add_edge("anomaly_agent",                 "prognostics_agent")
    graph.add_edge("prognostics_agent",             "evidence_retrieval_agent")
    graph.add_edge("evidence_retrieval_agent",      "diagnosis_agent")
    graph.add_edge("diagnosis_agent",               "evidence_verification_agent")
    graph.add_edge("evidence_verification_agent",   "operational_impact_agent")
    graph.add_edge("operational_impact_agent",      "maintenance_strategy_agent")
    graph.add_edge("maintenance_strategy_agent",    "plant_priority_agent")
    graph.add_edge("plant_priority_agent",          "communication_agent")
    graph.add_edge("communication_agent",           "finalize_report")
    graph.add_edge("finalize_report",               END)

    return graph.compile()


# Module-level singleton — compiled once, reused across requests
_compiled_graph = None


def get_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
