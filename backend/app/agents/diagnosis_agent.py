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
    deviation = a.deviation_percent or 0.0
    if deviation >= 20:
        severity_label = "SEVERE exceedance"
    elif deviation >= 10:
        severity_label = "SIGNIFICANT exceedance"
    else:
        severity_label = "exceedance"
    return (
        f"ACTIVE ANOMALY: sensor '{a.sensor}' value={a.value} — "
        f"{severity_label} of threshold {a.threshold} by {deviation:.1f}%. "
        f"This is confirmed sensor evidence, not a prediction."
    )


_MACHINE_TYPE_FAILURE_MODES: dict[str, str] = {
    "robotic arm": (
        "For a Robotic Arm, thermal anomalies suggest: servo motor overheating, "
        "controller board thermal stress, joint bearing lubrication failure, or cooling fan blockage. "
        "Vibration anomalies suggest: gear backlash, encoder drift, or structural resonance. "
        "Joint calibration errors in history correlate with bearing wear or encoder fouling."
    ),
    "centrifuge": (
        "For a Centrifuge, vibration anomalies suggest: rotor imbalance, bearing failure, or shaft misalignment. "
        "Temperature anomalies suggest: bearing overheating or motor thermal stress. "
        "Pressure anomalies suggest: seal leakage or process fluid contamination."
    ),
    "air compressor": (
        "For an Air Compressor, temperature anomalies suggest: valve failure, lubricant breakdown, or intercooler fouling. "
        "Pressure anomalies suggest: leaks, worn piston rings, or discharge valve failure. "
        "Vibration anomalies suggest: bearing wear or coupling misalignment."
    ),
    "cooling pump": (
        "For a Cooling Pump, vibration anomalies suggest: cavitation, impeller wear, or bearing failure. "
        "Temperature anomalies suggest: blocked flow or bearing overheating. "
        "Pressure drops suggest: impeller fouling or internal leakage."
    ),
    "generator": (
        "For a Generator, temperature anomalies suggest: winding insulation degradation or cooling system failure. "
        "Vibration anomalies suggest: rotor eccentricity, bearing wear, or coupling fault. "
        "RPM anomalies suggest: governor fault or load regulation issue."
    ),
    "milling machine": (
        "For a Milling Machine, vibration anomalies suggest: spindle bearing wear, tool imbalance, or chuck runout. "
        "Temperature anomalies suggest: lubrication failure or overloaded drive. "
        "Load anomalies suggest: excessive cutting depth or dull tooling."
    ),
}


def _build_machine_type_hints(state: VulcanOpsState) -> str:
    if not state.machine_context:
        return ""
    mtype = (state.machine_context.machine_type or "").lower()
    for key, hint in _MACHINE_TYPE_FAILURE_MODES.items():
        if key in mtype:
            return f"\nMACHINE-TYPE FAILURE MODE CONTEXT:\n{hint}"
    return ""


def _build_rul_summary(state: VulcanOpsState) -> str:
    if not state.rul_prediction:
        return "RUL not calculated."
    r = state.rul_prediction
    rul_h = r.remaining_useful_life_hours
    if rul_h is not None:
        if rul_h < 4:
            urgency = "IMMINENT FAILURE — intervention required now"
        elif rul_h < 24:
            urgency = "CRITICAL window — failure expected within 24h"
        elif rul_h < 168:
            urgency = "HIGH urgency — failure expected within 1 week"
        else:
            urgency = "Elevated concern"
        return (
            f"RUL {rul_h}h ({urgency}). "
            f"Confidence {r.confidence}. Basis: {r.basis}"
        )
    return f"RUL not quantified. Basis: {r.basis}"


def _build_history_summary(state: VulcanOpsState) -> str:
    if not state.maintenance_history:
        return "No maintenance history available."
    lines = []
    for i, record in enumerate(state.maintenance_history[:5], 1):
        parts = [
            f"date={record.date.isoformat() if record.date else 'unknown'}",
        ]
        if record.failure_mode:
            parts.append(f"failure_mode={record.failure_mode}")
        if record.action_taken:
            parts.append(f"action={record.action_taken}")
        if record.engineer:
            parts.append(f"engineer={record.engineer}")
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

MACHINE: {machine_desc}{_build_machine_type_hints(state)}

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
1. Base your diagnosis on the evidence above. Sensor threshold exceedances and RUL windows ARE evidence — treat them as facts.
2. Name the most specific probable failure mode the evidence supports. Use component-level language (e.g., "servo motor thermal overload", "bearing lubrication failure") not generic phrases.
3. Cite the evidence in 'reasoning' (e.g., 'temperature 15.9% above WARNING threshold', 'RUL 2h indicates imminent failure', 'maintenance history shows joint calibration on 2026-06-10').
4. Set confidence honestly based on evidence strength — but always give your best specific hypothesis. The system will label low-confidence outputs as 'cautious' or 'preliminary' automatically.
5. Only use root_cause='manual inspection required' when there is genuinely NO sensor, history, or documentary evidence to form even a preliminary hypothesis.

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
