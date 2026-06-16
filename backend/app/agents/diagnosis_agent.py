"""
Diagnosis Agent — LLM Agent #1.

Calls llm_service.generate_diagnosis() which routes to qwen/qwen3-8b via OpenRouter.
This file does NOT call any external API directly.

Input  : state.machine_context, state.sensor_readings (latest),
         state.anomaly, state.rul_prediction, state.retrieved_evidence
Output : AgentResult.data = {
    "root_cause": str,
    "failure_mode": str,
    "reasoning": str,
    "confidence": float,
    "evidence_used": list[str],
    "llm_telemetry": dict
}
"""

from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState
from app.services.llm_service import llm_service


def _build_sensor_summary(state: VulcanOpsState) -> str:
    if not state.sensor_readings:
        return "No sensor data available."

    readings = sorted(state.sensor_readings, key=lambda r: r.timestamp)
    latest = readings[-1]

    # Latest snapshot
    parts = []
    for field in ("temperature", "vibration", "pressure", "load", "rpm"):
        val = getattr(latest, field, None)
        if val is not None:
            parts.append(f"{field}={val}")
    summary = f"Latest reading ({latest.timestamp.isoformat()}): {', '.join(parts)}\n"

    # Recent trend: min/max over the last N readings for each field
    window = readings[-20:] if len(readings) >= 20 else readings
    summary += f"Trend window: last {len(window)} readings ({window[0].timestamp.isoformat()} to {window[-1].timestamp.isoformat()}).\n"
    for field in ("temperature", "vibration", "pressure", "load", "rpm"):
        values = [getattr(r, field) for r in window if getattr(r, field, None) is not None]
        if values:
            summary += f"  {field}: min={min(values):.2f}, max={max(values):.2f}, mean={sum(values)/len(values):.2f}\n"
    return summary.strip()


def _build_anomaly_summary(state: VulcanOpsState) -> str:
    if not state.anomaly or not state.anomaly.detected:
        return "No anomaly detected."
    a = state.anomaly
    return (
        f"Anomaly on sensor '{a.sensor}': value={a.value}, "
        f"threshold={a.threshold}, deviation={a.deviation_percent}%"
    )


def _build_rul_summary(state: VulcanOpsState) -> str:
    if not state.rul_prediction:
        return "RUL not calculated."
    r = state.rul_prediction
    return (
        f"Estimated remaining useful life: {r.remaining_useful_life_hours}h "
        f"(confidence {r.confidence}). Basis: {r.basis}"
    )


def _build_history_summary(state: VulcanOpsState) -> str:
    if not state.maintenance_history:
        return "No maintenance history available."
    lines = []
    for i, record in enumerate(state.maintenance_history[:5], 1):
        parts = [
            f"date={record.maintenance_date.isoformat() if record.maintenance_date else 'unknown'}",
        ]
        if record.failure_mode:
            parts.append(f"failure_mode={record.failure_mode}")
        if record.action_taken:
            parts.append(f"action={record.action_taken}")
        if record.technician_notes:
            parts.append(f"notes={record.technician_notes}")
        lines.append(f"[{i}] {', '.join(parts)}")
    return "\n".join(lines)


def _build_evidence_summary(state: VulcanOpsState) -> str:
    if not state.retrieved_evidence:
        return "No documentary evidence available."
    lines = []
    for i, ev in enumerate(state.retrieved_evidence[:6], 1):
        src = ev.get("source", "unknown")
        src_type = ev.get("source_type", "document")
        score = ev.get("relevance_score", 0.0)
        chunk = ev.get("chunk", "")
        lines.append(
            f"[{i}] {src_type.upper()} '{src}' (relevance={score:.2f}):\n{chunk}"
        )
    return "\n\n".join(lines)


def _build_prompt(state: VulcanOpsState) -> str:
    machine = state.machine_context
    machine_desc = (
        f"{machine.machine_name} ({machine.machine_type}) "
        f"at {machine.plant} — {machine.location}, "
        f"criticality={machine.criticality.value}"
        if machine
        else "Machine details unavailable"
    )

    return f"""Industrial root cause analysis request.

MACHINE: {machine_desc}

ANOMALY FINDINGS:
{_build_anomaly_summary(state)}

SENSOR DATA:
{_build_sensor_summary(state)}

REMAINING USEFUL LIFE:
{_build_rul_summary(state)}

MAINTENANCE HISTORY (most recent):
{_build_history_summary(state)}

DOCUMENTARY EVIDENCE:
{_build_evidence_summary(state)}

INSTRUCTIONS:
1. Base your diagnosis ONLY on the evidence above.
2. Cite the evidence that supports your conclusion in the 'reasoning' field (e.g., 'temperature rose from X to Y', 'manual states Z').
3. If evidence supports a specific component or system failure, name it explicitly.
4. Set confidence honestly: high only when evidence is strong and consistent; moderate when evidence suggests but does not prove a cause; low when evidence is weak.
5. If confidence is below 0.50, set root_cause='manual inspection required' and failure_mode='insufficient evidence'.

Return JSON only."""


async def run(state: VulcanOpsState) -> AgentResult:
    if not state.sensor_readings and not state.anomaly:
        return AgentResult(
            status="error",
            data={},
            errors=["Diagnosis requires at least sensor_readings or anomaly data"],
        )

    result = await llm_service.generate_diagnosis(_build_prompt(state))
    telemetry = result.get("_telemetry", {})

    return AgentResult(
        status="success",
        data={
            "root_cause":    result["root_cause"],
            "failure_mode":  result["failure_mode"],
            "reasoning":     result["reasoning"],
            "confidence":    result["confidence"],
            "evidence_used": result["evidence_used"],
            "llm_telemetry": telemetry,
        },
    )
