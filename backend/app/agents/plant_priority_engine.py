"""
Plant Priority Engine — deterministic fleet priority scoring.

No LLM.

Input  : state.machine_context, state.anomaly, state.rul_prediction, state.impact
Output : AgentResult.data = {
    "priority_score": float,    # 0 – 100
    "priority_rank": str,       # "P1" | "P2" | "P3" | "P4"
    "rank_label": str,          # human-readable label
    "score_breakdown": dict     # per-factor contribution
}

Scoring formula (weighted sum → normalised to 0–100):
  criticality  35%
  severity     25%
  rul          25%
  risk_level   15%
"""

from app.agents.base import AgentResult
from app.core.enums import MachineCriticality, RiskLevel
from app.core.state_contract import VulcanOpsState

_CRITICALITY_SCORES: dict[str, float] = {
    MachineCriticality.CRITICAL.value: 4.0,
    MachineCriticality.HIGH.value:     3.0,
    MachineCriticality.MEDIUM.value:   2.0,
    MachineCriticality.LOW.value:      1.0,
}

_SEVERITY_SCORES: dict[str, float] = {
    "critical": 4.0,
    "warning":  2.5,
    "normal":   1.0,
}

_RISK_SCORES: dict[str, float] = {
    RiskLevel.CRITICAL.value: 4.0,
    RiskLevel.HIGH.value:     3.0,
    RiskLevel.MEDIUM.value:   2.0,
    RiskLevel.LOW.value:      1.0,
}

_WEIGHTS = {
    "criticality": 0.35,
    "severity":    0.25,
    "rul":         0.25,
    "risk":        0.15,
}

_RANK_LABELS: dict[str, str] = {
    "P1": "Emergency — immediate action required",
    "P2": "Urgent — action required within 24 hours",
    "P3": "Scheduled — action required within 7 days",
    "P4": "Routine — include in next maintenance cycle",
}


def _rul_score(hours: float | None) -> float:
    """Map RUL hours to a 1–4 urgency score (higher = more urgent)."""
    if hours is None:
        return 2.0  # uncertain
    if hours < 24:
        return 4.0
    if hours < 72:
        return 3.0
    if hours < 168:
        return 2.0
    return 1.0


def run(state: VulcanOpsState) -> AgentResult:
    missing: list[str] = []
    if not state.machine_context:
        missing.append("machine_context")
    if missing:
        return AgentResult(
            status="error",
            data={},
            errors=[f"plant_priority_engine requires: {', '.join(missing)}"],
        )

    criticality_val = state.machine_context.criticality.value
    c_score = _CRITICALITY_SCORES.get(criticality_val, 2.0)

    severity = "normal"
    if state.anomaly and state.anomaly.detected:
        if state.anomaly.deviation_percent and state.anomaly.deviation_percent > 15:
            severity = "critical"
        else:
            severity = "warning"
    s_score = _SEVERITY_SCORES.get(severity, 1.0)

    rul_hours = (
        state.rul_prediction.remaining_useful_life_hours
        if state.rul_prediction
        else None
    )
    r_score = _rul_score(rul_hours)

    risk_val = (
        state.impact.risk_level.value
        if state.impact and state.impact.risk_level
        else RiskLevel.MEDIUM.value
    )
    risk_score = _RISK_SCORES.get(risk_val, 2.0)

    # Weighted sum over [1, 4] range → normalise to [0, 100]
    raw_score = (
        c_score  * _WEIGHTS["criticality"]
        + s_score  * _WEIGHTS["severity"]
        + r_score  * _WEIGHTS["rul"]
        + risk_score * _WEIGHTS["risk"]
    )
    # raw_score is in [1.0, 4.0]; map to [0, 100]
    priority_score = round((raw_score - 1.0) / 3.0 * 100.0, 1)

    if priority_score >= 75:
        priority_rank = "P1"
    elif priority_score >= 50:
        priority_rank = "P2"
    elif priority_score >= 25:
        priority_rank = "P3"
    else:
        priority_rank = "P4"

    return AgentResult(
        status="success",
        data={
            "priority_score": priority_score,
            "priority_rank": priority_rank,
            "rank_label": _RANK_LABELS[priority_rank],
            "score_breakdown": {
                "criticality": {"raw": c_score, "weight": _WEIGHTS["criticality"]},
                "severity":    {"raw": s_score, "weight": _WEIGHTS["severity"]},
                "rul":         {"raw": r_score, "weight": _WEIGHTS["rul"]},
                "risk_level":  {"raw": risk_score, "weight": _WEIGHTS["risk"]},
            },
        },
    )
