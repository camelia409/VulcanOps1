"""
Communication Agent — LLM Agent #2.

Calls llm_service.generate_role_reports() which routes to qwen/qwen3-4b via OpenRouter.
Generates three role-specific natural-language summaries from the full state
in a single LLM call.

Input  : full VulcanOpsState (post-analysis, post-decision)
Output : AgentResult.data = {
    "engineer_summary":    str,   # 150–200 words for field technician
    "supervisor_summary":  str,   # 150–200 words for shift supervisor
    "manager_summary":     str,   # 150–200 words for plant management
    "llm_telemetry":       dict
}
"""

from typing import Any

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState
from app.services.llm_service import llm_service


def _safe(value: Any, fallback: str = "not available") -> str:
    return fallback if value is None else str(value)


def _build_state_digest(state: VulcanOpsState) -> str:
    lines: list[str] = []
    m = state.machine_context
    if m:
        lines.append(
            f"Machine: {m.machine_name} ({m.machine_type}) at {m.plant}, "
            f"{m.location}. Criticality: {m.criticality.value}."
        )

    a = state.anomaly
    if a and a.detected:
        lines.append(
            f"Anomaly: {a.sensor} reading {a.value} exceeds threshold "
            f"{a.threshold} by {a.deviation_percent}%."
        )
    else:
        lines.append("Anomaly: None detected.")

    r = state.rul_prediction
    if r:
        lines.append(
            f"Remaining Useful Life: {_safe(r.remaining_useful_life_hours)}h "
            f"(confidence {_safe(r.confidence)}). {_safe(r.basis)}."
        )

    d = state.diagnosis
    if d:
        lines.append(
            f"Root Cause: {_safe(d.root_cause)}. "
            f"Failure Mode: {_safe(d.failure_mode)}. "
            f"LLM Confidence: {_safe(d.confidence)}."
        )

    i = state.impact
    if i:
        cost_str = f"${i.estimated_cost_usd:,.0f}" if i.estimated_cost_usd else "TBD"
        lines.append(
            f"Risk Level: {_safe(i.risk_level)}. "
            f"Estimated Downtime: {_safe(i.estimated_downtime_hours)}h. "
            f"Cost Exposure: {cost_str}."
        )
        lines.append(f"Business Impact: {_safe(i.business_impact_summary)}.")
        if i.compliance_flags:
            lines.append(f"Compliance: {'; '.join(i.compliance_flags)}.")

    s = state.strategy
    if s:
        lines.append(
            f"Immediate Action: {_safe(s.recommended_action)}. "
            f"Priority: {_safe(s.priority)}. "
            f"Estimated Repair: {_safe(s.estimated_repair_hours)}h."
        )
        if s.parts_required:
            lines.append(f"Parts Required: {', '.join(s.parts_required)}.")
        lines.append(f"Safety Notes: {_safe(s.safety_notes)}.")
        lines.append(f"Resources: {_safe(s.resource_requirements)}.")

    return "\n".join(lines)


def _build_prompt(state: VulcanOpsState) -> str:
    digest = _build_state_digest(state)
    return f"""Write three operational reports for an industrial plant investigation.

INVESTIGATION SUMMARY:
{digest}

Produce one report per audience. Each report must be 150-200 words, specific, factual, and professional.

engineer: field engineer performing the repair — cover fault description, what to check first, parts needed, safety precautions, post-repair monitoring.
supervisor: shift supervisor coordinating the response — cover operational impact, resource requirements, production line effects, timeline, escalation chain.
manager: plant management — cover business risk, estimated cost, compliance obligations, strategic recommendation, programme implications.

Return JSON only."""


async def run(state: VulcanOpsState) -> AgentResult:
    required = [
        ("machine_context", state.machine_context),
        ("diagnosis",       state.diagnosis),
        ("impact",          state.impact),
        ("strategy",        state.strategy),
    ]
    missing = [name for name, val in required if val is None]
    if missing:
        return AgentResult(
            status="error",
            data={},
            errors=[
                f"communication_agent requires complete analysis state. "
                f"Missing: {', '.join(missing)}"
            ],
        )

    result = await llm_service.generate_role_reports(_build_prompt(state))
    telemetry = result.get("_telemetry", {})

    return AgentResult(
        status="success",
        data={
            "engineer_summary":   result["engineer"],
            "supervisor_summary": result["supervisor"],
            "manager_summary":    result["manager"],
            "llm_telemetry":      telemetry,
        },
    )
