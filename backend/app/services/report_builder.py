"""
Report Builder — pure serializer: converts VulcanOpsState into a frontend-friendly
response dict.

This module contains NO business logic, thresholds, overrides, or sanitizers.
All uncertainty correction is applied by graph_builder._finalize_node before
this module is called. This module reads the state and serialises it.
"""

from typing import Any

from app.core.state_contract import VulcanOpsState


def _safe(value: Any, fallback: Any = None) -> Any:
    return fallback if value is None else value


def build_machine_summary(state: VulcanOpsState) -> dict[str, Any]:
    """Compact machine snapshot embedded in every report."""
    m = state.machine_context
    if not m:
        return {"machine_id": str(state.active_machine_id)}
    return {
        "machine_id":   str(m.machine_id),
        "machine_name": m.machine_name,
        "machine_type": m.machine_type,
        "plant":        m.plant,
        "location":     m.location,
        "criticality":  m.criticality.value,
        "status":       m.status.value,
    }


def build_single_report(state: VulcanOpsState) -> dict[str, Any]:
    """
    Convert one VulcanOpsState into a single machine report dict.

    State has already been corrected by graph_builder._finalize_node before
    this function is called — no further overrides are applied here.

    Shape:
    {
        machine             : {...},
        risk_level          : str | None,
        root_cause          : str | None,
        failure_mode        : str | None,
        diagnosis_confidence: float | None,
        recommended_action  : str | None,
        priority            : str | None,
        rul_hours           : float | None,
        estimated_downtime_hours: float | None,
        estimated_cost_usd  : float | None,
        parts_required      : list[str],
        anomaly             : {...} | None,
        verification        : {...} | None,
        engineer_report     : str | None,
        supervisor_report   : str | None,
        manager_report      : str | None,
        execution_trace     : [...],
        pipeline_errors     : int,
        has_errors          : bool,
    }
    """
    anomaly_block: dict | None = None
    if state.anomaly:
        anomaly_block = {
            "detected":          state.anomaly.detected,
            "sensor":            state.anomaly.sensor,
            "value":             state.anomaly.value,
            "threshold":         state.anomaly.threshold,
            "deviation_percent": state.anomaly.deviation_percent,
        }

    verification_block: dict | None = None
    if state.verification:
        verification_block = {
            "verified":                  state.verification.verified,
            "verification_notes":        state.verification.verification_notes,
            "contradictions":            state.verification.contradictions,
            "evidence_score":            state.verification.evidence_score,
            "history_score":             state.verification.history_score,
            "combined_score":            state.verification.combined_score,
        }

    engineer_text = (
        state.role_reports.engineer.safety_notes
        if state.role_reports and state.role_reports.engineer
        else None
    )
    supervisor_text = (
        state.role_reports.supervisor.resource_requirements
        if state.role_reports and state.role_reports.supervisor
        else None
    )
    manager_text = (
        state.role_reports.manager.business_impact
        if state.role_reports and state.role_reports.manager
        else None
    )

    # Report-quality telemetry from the finalizer (if available)
    telemetry = state.final_report or {}

    return {
        "machine":              build_machine_summary(state),
        "risk_level":           state.impact.risk_level.value if state.impact and state.impact.risk_level else None,
        "root_cause":           state.diagnosis.root_cause if state.diagnosis else None,
        "failure_mode":         state.diagnosis.failure_mode if state.diagnosis else None,
        "diagnosis_confidence": state.diagnosis.confidence if state.diagnosis else None,
        "recommended_action":   state.strategy.recommended_action if state.strategy else None,
        "priority":             state.priority.value if state.priority else None,
        "rul_hours":            state.rul_prediction.remaining_useful_life_hours if state.rul_prediction else None,
        "estimated_downtime_hours": state.impact.estimated_downtime_hours if state.impact else None,
        "estimated_cost_usd":   state.impact.estimated_cost_usd if state.impact else None,
        "parts_required":           state.strategy.parts_required if state.strategy else [],
        "procurement_strategy":     state.strategy.procurement_strategy if state.strategy else None,
        "constraint_violations":    state.strategy.constraint_violations if state.strategy else [],
        "anomaly":              anomaly_block,
        "verification":         verification_block,
        "engineer_report":      engineer_text,
        "supervisor_report":    supervisor_text,
        "manager_report":       manager_text,
        "execution_trace":      state.execution_trace,
        "execution_plan":       state.execution_plan.model_dump() if state.execution_plan else None,
        "pipeline_errors":      len(state.errors),
        "has_errors":           len(state.errors) > 0,
        "diagnosis": {
            "root_cause":      state.diagnosis.root_cause if state.diagnosis else None,
            "failure_mode":    state.diagnosis.failure_mode if state.diagnosis else None,
            "confidence":      state.diagnosis.confidence if state.diagnosis else None,
            "reasoning_trace": [
                step.model_dump() for step in state.diagnosis.reasoning_trace
            ] if state.diagnosis else [],
        },
        # Quality telemetry so the UI can explain why a report is specific/cautious/fallback
        "evidence_score":       telemetry.get("evidence_score"),
        "history_score":        telemetry.get("history_score"),
        "fallback_used":        telemetry.get("fallback_used", False),
        "uncertainty_reason":   telemetry.get("uncertainty_reason"),
        "final_report_status":  telemetry.get("final_report_status"),
        "circuit_breaker_state": telemetry.get("circuit_breaker_state"),
        # Explainability & procurement gap intelligence
        "evidence_chain": telemetry.get("evidence_chain"),
        "explainability_score": telemetry.get("explainability_score"),
        "procurement_gap": telemetry.get("procurement_gap"),
        # Prior feedback injected into diagnosis
        "prior_feedback_considered": [
            {
                "verdict": fb.get("verdict"),
                "actual_root_cause": fb.get("actual_root_cause"),
                "notes": fb.get("notes"),
                "failure_mode": fb.get("failure_mode"),
            }
            for fb in state.prior_feedback
        ],
        # Verification cycle telemetry
        "verification_contradictions":  telemetry.get("verification_contradictions", []),
        "verification_revision_count":  telemetry.get("verification_revision_count", 0),
        "verification_recommendation":  state.verification_recommendation,
    }


def build_response(
    *,
    title: str,
    intent: str,
    query: str,
    reports: list[dict[str, Any]],
    machines: list[dict[str, Any]] | None = None,
    routing_confidence: float = 1.0,
) -> dict[str, Any]:
    """
    Top-level response envelope returned by the chat endpoint.

    Shape:
    {
        title              : str,
        intent             : str,
        query              : str,
        routing_confidence : float,
        reports            : [build_single_report(), ...],
        machines           : [...] | None,
        report_count       : int,
    }
    """
    return {
        "title":              title,
        "intent":             intent,
        "query":              query,
        "routing_confidence": routing_confidence,
        "reports":            reports,
        "machines":           machines,
        "report_count":       len(reports),
    }
