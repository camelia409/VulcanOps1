"""
Operational Impact Agent — deterministic cost and production risk assessment.

No LLM.

Input  : state.machine_context, state.anomaly, state.rul_prediction,
         state.maintenance_history
Output : AgentResult.data = {
    "risk_level": str,
    "estimated_downtime_hours": float,
    "estimated_cost_usd": float,
    "production_impact": str,
    "revenue_risk": str,
    "affected_production_lines": list[str],
    "compliance_flags": list[str],
    "business_impact_summary": str
}
"""

from app.agents.base import AgentResult
from app.core.enums import MachineCriticality, RiskLevel
from app.core.state_contract import VulcanOpsState

# Baseline cost per hour of downtime by criticality (USD)
_DOWNTIME_COST_PER_HOUR: dict[str, float] = {
    MachineCriticality.CRITICAL.value: 25_000.0,
    MachineCriticality.HIGH.value:     10_000.0,
    MachineCriticality.MEDIUM.value:    4_000.0,
    MachineCriticality.LOW.value:       1_000.0,
}

# Downtime multiplier by anomaly severity
_SEVERITY_MULTIPLIER: dict[str, float] = {
    "critical": 1.8,
    "warning":  1.3,
    "normal":   1.0,
}

# Risk level derivation matrix: (criticality, severity) → RiskLevel
_RISK_MATRIX: dict[tuple[str, str], RiskLevel] = {
    ("critical", "critical"): RiskLevel.CRITICAL,
    ("critical", "warning"):  RiskLevel.HIGH,
    ("critical", "normal"):   RiskLevel.MEDIUM,
    ("high",     "critical"): RiskLevel.CRITICAL,
    ("high",     "warning"):  RiskLevel.HIGH,
    ("high",     "normal"):   RiskLevel.MEDIUM,
    ("medium",   "critical"): RiskLevel.HIGH,
    ("medium",   "warning"):  RiskLevel.MEDIUM,
    ("medium",   "normal"):   RiskLevel.LOW,
    ("low",      "critical"): RiskLevel.MEDIUM,
    ("low",      "warning"):  RiskLevel.LOW,
    ("low",      "normal"):   RiskLevel.LOW,
}

_DEFAULT_DOWNTIME_HOURS = 8.0  # used when no maintenance history available


def run(state: VulcanOpsState) -> AgentResult:
    if not state.machine_context:
        return AgentResult(
            status="error",
            data={},
            errors=["machine_context is required for operational impact assessment"],
        )

    criticality = state.machine_context.criticality.value
    severity = state.anomaly.detected and "critical" or "normal"
    if state.anomaly:
        # Determine severity from anomaly data
        if not state.anomaly.detected:
            severity = "normal"
        elif state.anomaly.deviation_percent and state.anomaly.deviation_percent > 15:
            severity = "critical"
        else:
            severity = "warning"

    # ── Downtime estimate ──
    if state.maintenance_history:
        avg_historical = sum(r.downtime_hours for r in state.maintenance_history) / len(
            state.maintenance_history
        )
        base_downtime = avg_historical
    else:
        base_downtime = _DEFAULT_DOWNTIME_HOURS

    multiplier = _SEVERITY_MULTIPLIER.get(severity, 1.0)

    # RUL-adjusted: if failure is imminent, reduce repair window margin
    if state.rul_prediction and state.rul_prediction.remaining_useful_life_hours is not None:
        rul_h = state.rul_prediction.remaining_useful_life_hours
        if rul_h < 24:
            multiplier *= 1.4  # emergency repair premium
        elif rul_h < 72:
            multiplier *= 1.1

    estimated_downtime = round(base_downtime * multiplier, 1)

    # ── Cost ──
    cost_per_hour = _DOWNTIME_COST_PER_HOUR.get(criticality, 4_000.0)
    estimated_cost = round(estimated_downtime * cost_per_hour, 2)

    # ── Risk level ──
    risk_level = _RISK_MATRIX.get(
        (criticality, severity),
        RiskLevel.MEDIUM,
    )

    # ── Qualitative assessments ──
    production_impact_map = {
        RiskLevel.CRITICAL: "Severe — full production halt expected",
        RiskLevel.HIGH:     "High — significant throughput reduction",
        RiskLevel.MEDIUM:   "Moderate — partial production degradation",
        RiskLevel.LOW:      "Low — minimal operational disruption",
    }
    revenue_risk_map = {
        RiskLevel.CRITICAL: "Critical — immediate revenue loss and SLA breach risk",
        RiskLevel.HIGH:     "High — significant revenue exposure within 72 hours",
        RiskLevel.MEDIUM:   "Medium — revenue impact if unaddressed this week",
        RiskLevel.LOW:      "Low — contained within normal maintenance budget",
    }

    production_impact = production_impact_map[risk_level]
    revenue_risk = revenue_risk_map[risk_level]

    # ── Compliance flags ──
    compliance_flags: list[str] = []
    if risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
        compliance_flags.append("HSE notification may be required for critical equipment failure")
    if estimated_downtime > 24:
        compliance_flags.append("Extended downtime (>24h) — insurance and contractual notification required")

    # ── Business summary ──
    plant = state.machine_context.plant
    machine_name = state.machine_context.machine_name
    business_impact_summary = (
        f"{machine_name} at {plant} is facing {risk_level.value} risk. "
        f"Estimated downtime: {estimated_downtime}h with a cost exposure of "
        f"${estimated_cost:,.0f}. {production_impact}."
    )

    return AgentResult(
        status="success",
        data={
            "risk_level": risk_level.value,
            "estimated_downtime_hours": estimated_downtime,
            "estimated_cost_usd": estimated_cost,
            "production_impact": production_impact,
            "revenue_risk": revenue_risk,
            "affected_production_lines": [f"{plant} — {state.machine_context.location}"],
            "compliance_flags": compliance_flags,
            "business_impact_summary": business_impact_summary,
        },
    )
