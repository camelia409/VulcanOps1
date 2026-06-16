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
    latest = max(state.sensor_readings, key=lambda r: r.timestamp)
    parts = []
    for field in ("temperature", "vibration", "pressure", "load", "rpm"):
        val = getattr(latest, field, None)
        if val is not None:
            parts.append(f"{field}={val}")
    return f"Latest reading ({latest.timestamp.isoformat()}): {', '.join(parts)}"


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


def _build_evidence_summary(state: VulcanOpsState) -> str:
    if not state.retrieved_evidence:
        return "No documentary evidence available."
    lines = []
    for i, ev in enumerate(state.retrieved_evidence[:4], 1):
        src = ev.get("source", "unknown")
        chunk = ev.get("chunk", "")[:300]
        lines.append(f"[{i}] {src}: {chunk}")
    return "\n".join(lines)


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

SENSOR DATA:
{_build_sensor_summary(state)}

ANOMALY FINDINGS:
{_build_anomaly_summary(state)}

REMAINING USEFUL LIFE:
{_build_rul_summary(state)}

DOCUMENTARY EVIDENCE:
{_build_evidence_summary(state)}

Analyse the above data and identify the root cause of the fault. Return JSON only."""


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
