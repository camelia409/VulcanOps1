"""
Maintenance Strategy Agent — deterministic action plan generation.

No LLM.

Input  : state.machine_context, state.anomaly, state.rul_prediction, state.impact
Output : AgentResult.data = {
    "immediate_action": str,
    "medium_term_action": str,
    "monitoring_plan": str,
    "priority": str,            # MaintenancePriority value
    "parts_required": list[str],
    "estimated_repair_hours": float,
    "safety_notes": str,
    "resource_requirements": str
}
"""

from app.agents.base import AgentResult
from app.core.enums import MaintenancePriority, RiskLevel
from app.core.state_contract import VulcanOpsState

# Maps (risk_level, rul_hours_bucket) → MaintenancePriority
# rul_bucket: "imminent" <24h, "near" <72h, "medium" <168h, "long" ≥168h
def _rul_bucket(hours: float | None) -> str:
    if hours is None:
        return "long"
    if hours < 24:
        return "imminent"
    if hours < 72:
        return "near"
    if hours < 168:
        return "medium"
    return "long"


_PRIORITY_MATRIX: dict[tuple[str, str], MaintenancePriority] = {
    ("critical", "imminent"): MaintenancePriority.EMERGENCY,
    ("critical", "near"):     MaintenancePriority.EMERGENCY,
    ("critical", "medium"):   MaintenancePriority.URGENT,
    ("critical", "long"):     MaintenancePriority.URGENT,
    ("high",     "imminent"): MaintenancePriority.EMERGENCY,
    ("high",     "near"):     MaintenancePriority.URGENT,
    ("high",     "medium"):   MaintenancePriority.URGENT,
    ("high",     "long"):     MaintenancePriority.SCHEDULED,
    ("medium",   "imminent"): MaintenancePriority.URGENT,
    ("medium",   "near"):     MaintenancePriority.SCHEDULED,
    ("medium",   "medium"):   MaintenancePriority.SCHEDULED,
    ("medium",   "long"):     MaintenancePriority.ROUTINE,
    ("low",      "imminent"): MaintenancePriority.SCHEDULED,
    ("low",      "near"):     MaintenancePriority.ROUTINE,
    ("low",      "medium"):   MaintenancePriority.ROUTINE,
    ("low",      "long"):     MaintenancePriority.ROUTINE,
}

_IMMEDIATE_ACTIONS: dict[MaintenancePriority, str] = {
    MaintenancePriority.EMERGENCY: (
        "Initiate immediate controlled shutdown. Notify shift supervisor and "
        "on-call maintenance team. Isolate the machine from the production line "
        "following lockout/tagout (LOTO) procedure."
    ),
    MaintenancePriority.URGENT: (
        "Schedule maintenance within 24 hours. Reduce machine load by 20% and "
        "increase monitoring frequency to 15-minute intervals. Alert maintenance "
        "lead to prepare repair team and parts."
    ),
    MaintenancePriority.SCHEDULED: (
        "Schedule maintenance within the next planned maintenance window (within 7 days). "
        "Continue operation with enhanced monitoring. Flag for next shift handover."
    ),
    MaintenancePriority.ROUTINE: (
        "Log finding and include in next scheduled preventive maintenance cycle. "
        "No immediate operational change required."
    ),
}

_MEDIUM_TERM_ACTIONS: dict[MaintenancePriority, str] = {
    MaintenancePriority.EMERGENCY: (
        "After emergency repair, perform full machine inspection and recalibration. "
        "Review and update maintenance schedule. Conduct post-incident review within 48 hours."
    ),
    MaintenancePriority.URGENT: (
        "Following repair, implement enhanced monitoring for 30 days. "
        "Review maintenance interval and consider condition-based maintenance upgrade."
    ),
    MaintenancePriority.SCHEDULED: (
        "During scheduled maintenance, perform full component inspection. "
        "Update service records and reassess monitoring thresholds."
    ),
    MaintenancePriority.ROUTINE: (
        "Include sensor calibration check in next preventive maintenance. "
        "Review historical trends at next monthly maintenance review."
    ),
}

_REPAIR_HOURS: dict[MaintenancePriority, float] = {
    MaintenancePriority.EMERGENCY: 12.0,
    MaintenancePriority.URGENT:    6.0,
    MaintenancePriority.SCHEDULED: 4.0,
    MaintenancePriority.ROUTINE:   2.0,
}

_SAFETY_NOTES: dict[MaintenancePriority, str] = {
    MaintenancePriority.EMERGENCY: (
        "CRITICAL SAFETY: Full LOTO required before any maintenance activity. "
        "Two-person rule applies. Wear appropriate PPE. Verify zero energy state before work."
    ),
    MaintenancePriority.URGENT: (
        "LOTO required. Ensure machine is fully de-energised before inspection. "
        "PPE mandatory. Report any unexpected findings immediately."
    ),
    MaintenancePriority.SCHEDULED: (
        "Standard LOTO procedure applies. PPE required. Follow site safety protocol."
    ),
    MaintenancePriority.ROUTINE: (
        "Follow standard maintenance safety protocol. PPE required for all maintenance tasks."
    ),
}


def _build_monitoring_plan(state: VulcanOpsState, priority: MaintenancePriority) -> str:
    sensors_flagged = []
    if state.anomaly and state.anomaly.sensor:
        sensors_flagged.append(state.anomaly.sensor)

    interval_map = {
        MaintenancePriority.EMERGENCY: "5 minutes",
        MaintenancePriority.URGENT:    "15 minutes",
        MaintenancePriority.SCHEDULED: "1 hour",
        MaintenancePriority.ROUTINE:   "4 hours",
    }
    interval = interval_map[priority]
    sensors_str = (
        f"Focus on {', '.join(sensors_flagged)}. " if sensors_flagged
        else "Monitor all sensors. "
    )
    return (
        f"{sensors_str}Increase telemetry collection to {interval} intervals until resolved. "
        "Set alert thresholds 10% below current anomaly values to catch early deterioration."
    )


def _estimate_parts(state: VulcanOpsState) -> list[str]:
    """Derive likely parts from machine type, failure mode, and root cause keywords."""
    machine_type = (state.machine_context.machine_type if state.machine_context else "").lower()
    failure_mode = (state.diagnosis.failure_mode if state.diagnosis else "").lower()
    root_cause = (state.diagnosis.root_cause if state.diagnosis else "").lower()
    sensor = (state.anomaly.sensor if state.anomaly else "")

    combined = f"{failure_mode} {root_cause}"
    parts: list[str] = []

    if "bearing" in combined or "vibration" == sensor:
        parts += ["Replacement bearings (OEM spec)", "Bearing grease / lubricant"]
    if "seal" in combined or "pressure" == sensor:
        parts += ["Seal kit", "O-rings"]
    if "thermal" in combined or "temperature" == sensor:
        parts += ["Thermal gaskets", "Cooling fluid"]
    if "coupling" in combined:
        parts += ["Coupling alignment kit", "Replacement coupling inserts"]
    if "lubrication" in combined or "oil" in combined:
        parts += ["Correct grade lubricant / oil", "Oil filter"]
    if "pump" in machine_type:
        parts += ["Impeller inspection kit", "Mechanical seal"]
    if "motor" in machine_type:
        parts += ["Motor brushes / windings inspection", "Insulation resistance tester"]
    if "compressor" in machine_type:
        parts += ["Compressor valve set", "Filter elements"]

    return parts if parts else ["Parts to be determined by maintenance team following inspection"]


def run(state: VulcanOpsState) -> AgentResult:
    if not state.impact:
        return AgentResult(
            status="error",
            data={},
            errors=["impact assessment is required before generating maintenance strategy"],
        )

    risk_level_val = state.impact.risk_level.value if state.impact.risk_level else "medium"
    rul_hours = (
        state.rul_prediction.remaining_useful_life_hours
        if state.rul_prediction
        else None
    )
    bucket = _rul_bucket(rul_hours)
    priority = _PRIORITY_MATRIX.get((risk_level_val, bucket), MaintenancePriority.SCHEDULED)

    parts = _estimate_parts(state)
    monitoring_plan = _build_monitoring_plan(state, priority)

    machine_name = state.machine_context.machine_name if state.machine_context else "the machine"
    resource_requirements = (
        f"Maintenance team: 2 technicians minimum for {priority.value} priority work on {machine_name}. "
        f"Estimated repair window: {_REPAIR_HOURS[priority]}h. "
        "Ensure spare parts availability before scheduling."
    )

    return AgentResult(
        status="success",
        data={
            "immediate_action": _IMMEDIATE_ACTIONS[priority],
            "medium_term_action": _MEDIUM_TERM_ACTIONS[priority],
            "monitoring_plan": monitoring_plan,
            "priority": priority.value,
            "parts_required": parts,
            "estimated_repair_hours": _REPAIR_HOURS[priority],
            "safety_notes": _SAFETY_NOTES[priority],
            "resource_requirements": resource_requirements,
        },
    )
