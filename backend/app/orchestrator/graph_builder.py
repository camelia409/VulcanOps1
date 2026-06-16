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

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

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
        result: AgentResult = evidence_retrieval_agent.run(state)
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
        return {
            "execution_trace": _append_trace(state, trace),
            "errors": errors,
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
    _telemetry: dict[str, Any] = {
        "evidence_score": evidence_score,
        "history_score": history_score,
        "diagnosis_confidence": _confidence,
        "verified": verified,
        "fallback_used": False,
        "uncertainty_reason": None,
        "circuit_breaker_state": "CLOSED",
        "final_report_status": disposition,
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

        # Override role report text fields after communication agent has set them
        rr = state.role_reports
        if rr.engineer:
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

    graph.add_node("anomaly_agent",                 _anomaly_node)
    graph.add_node("prognostics_agent",             _prognostics_node)
    graph.add_node("evidence_retrieval_agent",      _evidence_retrieval_node)
    graph.add_node("diagnosis_agent",               _diagnosis_node)
    graph.add_node("evidence_verification_agent",   _evidence_verification_node)
    graph.add_node("operational_impact_agent",      _operational_impact_node)
    graph.add_node("maintenance_strategy_agent",    _maintenance_strategy_node)
    graph.add_node("plant_priority_agent",          _plant_priority_node)
    graph.add_node("communication_agent",           _communication_node)
    graph.add_node("finalize_report",               _finalize_node)

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
