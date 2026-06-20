"""
Diagnosis Agent — LLM Agent #1 with native tool-calling ReAct reasoning.

The agent iterates up to 4 times. In each iteration it observes the current
state, decides what evidence would be most useful, and calls one of these tools:
  - retrieve_more(query: str)        → re-query the document_chunks index
  - get_sensor_history(sensor: str, hours: int)
                                     → longer history for a specific sensor
  - search_maintenance(failure_mode: str)
                                     → historical maintenance records by failure mode
  - conclude(...)                    → submit the final diagnosis

The full reasoning trace (thought + action + observation per iteration) is
stored in the diagnosis result so it can be displayed in the UI later.

JSON-in-prose parsing has been replaced with OpenAI-compatible native
tool-calling, so JSON parse errors are no longer a failure class.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text

from app.agents import evidence_retrieval_agent
from app.agents.base import AgentResult
from app.core.state_contract import (
    AnomalyDetail,
    DiagnosisResult,
    ReActStep,
    VulcanOpsState,
)
from app.db.session import AsyncSessionLocal
from app.models.maintenance_record import MaintenanceRecord
from app.models.sensor_reading import SensorReading
from app.services.llm_service import LLMError, llm_service

_MAX_ITERATIONS = 4

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "retrieve_more",
            "description": "Search ingested manuals, SOPs, and documents for a refined query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sensor_history",
            "description": "Pull historical readings for one sensor on this machine over N hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor": {
                        "type": "string",
                        "enum": ["temperature", "vibration", "pressure", "load", "rpm"],
                    },
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 720,
                    },
                },
                "required": ["sensor", "hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_maintenance",
            "description": "Search this machine's maintenance records by failure mode keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "failure_mode": {"type": "string"},
                },
                "required": ["failure_mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conclude",
            "description": "Submit the final diagnosis. Use when you have enough evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "failure_mode": {"type": "string"},
                    "root_cause": {"type": "string"},
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["failure_mode", "root_cause", "confidence", "reasoning"],
            },
        },
    },
]

_REACT_SYSTEM_PROMPT = (
    "You are an industrial reliability engineer performing iterative root cause analysis.\n"
    "Think step by step. Observe the evidence, decide what additional evidence would be most useful, "
    "then call exactly one tool. When you have enough evidence, call 'conclude'.\n\n"
    "CRITICAL RULES:\n"
    "- Call only one tool per turn.\n"
    "- Do not call the same tool with the same arguments twice. If a tool returns little value, try a different tool or conclude.\n"
    "- After each tool call you will see an Observation. Use that observation to choose a *different* next action; never repeat the same call.\n"
    "- Sensor threshold exceedances and RUL windows are facts, not predictions — treat them as evidence.\n"
    "- Be specific: name components and systems, not generic phrases.\n"
    "- Confidence must reflect evidence strength honestly.\n"
    "- If evidence is genuinely absent, conclude with low confidence and root_cause='manual inspection required'."
)


def _build_sensor_summary(state: VulcanOpsState) -> str:
    if not state.sensor_readings:
        return "No sensor data available."

    readings = sorted(state.sensor_readings, key=lambda r: r.timestamp)
    latest = readings[-1]

    parts = []
    for field in ("temperature", "vibration", "pressure", "load", "rpm"):
        val = getattr(latest, field, None)
        if val is not None:
            parts.append(f"{field}={val}")
    summary = f"Latest reading ({latest.timestamp.isoformat()}): {', '.join(parts)}\n"

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


def _build_evidence_summary(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "No documentary evidence available."
    lines = []
    for i, ev in enumerate(evidence[:6], 1):
        src = ev.get("source", "unknown")
        src_type = ev.get("source_type", "document")
        score = ev.get("relevance_score", 0.0)
        chunk = ev.get("chunk", "")
        lines.append(
            f"[{i}] {src_type.upper()} '{src}' (relevance={score:.2f}):\n{chunk}"
        )
    return "\n\n".join(lines)


def _build_feedback_block(state: VulcanOpsState) -> str:
    """Prepend a block of past engineer corrections when they exist in state."""
    feedback = state.prior_feedback
    if not feedback:
        return ""
    lines = [
        "[PRIOR ENGINEER FEEDBACK — relevant past corrections on this machine or "
        "this failure mode. Treat these as ground truth from the field; weigh them "
        "strongly when forming your diagnosis.]",
    ]
    for i, fb in enumerate(feedback, 1):
        parts: list[str] = [f"Case {i}:"]
        if fb.get("failure_mode"):
            parts.append(f"  failure_mode='{fb['failure_mode']}'")
        if fb.get("reported_root_cause"):
            parts.append(f"  original diagnosis='{fb['reported_root_cause']}'")
        if fb.get("verdict"):
            parts.append(f"  engineer verdict='{fb['verdict']}'")
        if fb.get("actual_root_cause"):
            parts.append(f"  actual root cause='{fb['actual_root_cause']}'")
        if fb.get("notes"):
            parts.append(f"  engineer notes='{fb['notes']}'")
        lines.append("\n".join(parts))
    lines.append("=" * 60)
    return "\n".join(lines) + "\n\n"


def _build_repass_header(state: VulcanOpsState) -> str:
    """Return a context block prepended when this is a revision re-pass."""
    if not state.verification_contradictions:
        return ""
    prior = state.diagnosis
    prior_fm = prior.failure_mode if prior else "unknown"
    prior_rc = prior.root_cause if prior else "unknown"
    contradiction_text = "\n".join(
        f"  - {c.get('contradiction', str(c)) if isinstance(c, dict) else str(c)}"
        for c in state.verification_contradictions
    )
    return (
        "⚠️  REVISION REQUEST — PRIOR DIAGNOSIS WAS CONTESTED\n"
        "An adversarial verification step found contradictions with your previous diagnosis.\n"
        f"  Previous failure_mode : {prior_fm}\n"
        f"  Previous root_cause   : {prior_rc}\n"
        f"Contradictions identified:\n{contradiction_text}\n\n"
        "Please reconsider your diagnosis, addressing each contradiction explicitly "
        "before calling 'conclude'.\n"
        "=" * 60 + "\n\n"
    )


def _build_initial_observation(state: VulcanOpsState) -> str:
    machine = state.machine_context
    machine_desc = (
        f"{machine.machine_name} ({machine.machine_type}) "
        f"at {machine.plant} — {machine.location}, "
        f"criticality={machine.criticality.value}"
        if machine
        else "Machine details unavailable"
    )

    feedback_block = _build_feedback_block(state)
    repass_header = _build_repass_header(state)
    return feedback_block + repass_header + f"""Industrial root cause analysis request.

MACHINE: {machine_desc}{_build_machine_type_hints(state)}

ANOMALY FINDINGS:
{_build_anomaly_summary(state)}

SENSOR DATA:
{_build_sensor_summary(state)}

REMAINING USEFUL LIFE:
{_build_rul_summary(state)}

MAINTENANCE HISTORY (most recent):
{_build_history_summary(state)}

DOCUMENTARY EVIDENCE (from initial retrieval):
{_build_evidence_summary(state.retrieved_evidence)}

Begin the investigation. Call a tool to gather more evidence, or call 'conclude' if you already have enough evidence."""


def _format_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "No additional documents found."
    lines = []
    for i, ev in enumerate(evidence, 1):
        src = ev.get("source", "unknown")
        score = ev.get("relevance_score", 0.0)
        chunk = ev.get("chunk", "")[:400]
        lines.append(f"[{i}] {src} (score={score:.2f}): {chunk}")
    return "\n".join(lines)


async def _retrieve_more(query: str, state: VulcanOpsState) -> str:
    """Re-query the document_chunks index with a refined query."""
    synthetic_state = state.model_copy(
        update={
            "anomaly": AnomalyDetail(
                detected=True,
                sensor=query,
            ),
        },
        deep=True,
    )
    result = await evidence_retrieval_agent.run(synthetic_state)
    evidence = result.data.get("retrieved_evidence", [])
    return _format_evidence(evidence[:3])


async def _get_sensor_history(sensor: str, hours: int, state: VulcanOpsState) -> str:
    """Pull recent sensor readings for the active machine and sensor."""
    machine_id = state.active_machine_id
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SensorReading)
            .where(SensorReading.machine_id == machine_id)
            .where(SensorReading.timestamp >= since)
            .order_by(SensorReading.timestamp.desc())
        )
        rows = result.scalars().all()

    if not rows:
        return f"No {sensor} readings in the last {hours}h."

    values = [getattr(r, sensor) for r in rows if getattr(r, sensor, None) is not None]
    if not values:
        return f"Sensor '{sensor}' has no data in the last {hours}h."

    stats = {
        "count": len(values),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
    }
    if len(values) > 1:
        stats["stddev"] = round(statistics.stdev(values), 2)

    last_5 = [
        f"{r.timestamp.isoformat()}: {getattr(r, sensor)}"
        for r in rows[:5]
        if getattr(r, sensor, None) is not None
    ]

    return (
        f"{sensor} history (last {hours}h): {json.dumps(stats)}. "
        f"Last 5 readings: {' | '.join(last_5)}"
    )


async def _search_maintenance(failure_mode: str, state: VulcanOpsState) -> str:
    """Search maintenance records for this machine by failure mode keyword."""
    machine_id = state.active_machine_id
    keyword = f"%{failure_mode}%"

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.machine_id == machine_id)
            .where(
                text("failure_mode ILIKE :keyword OR action_taken ILIKE :keyword")
            )
            .params(keyword=keyword)
            .order_by(MaintenanceRecord.date.desc())
            .limit(5)
        )
        rows = result.scalars().all()

    if not rows:
        return f"No maintenance records matching '{failure_mode}' for this machine."

    lines = []
    for i, r in enumerate(rows, 1):
        parts = [f"date={r.date.isoformat() if r.date else 'unknown'}"]
        if r.failure_mode:
            parts.append(f"failure_mode={r.failure_mode}")
        if r.action_taken:
            parts.append(f"action={r.action_taken}")
        if r.downtime_hours:
            parts.append(f"downtime={r.downtime_hours}h")
        lines.append(f"[{i}] {', '.join(parts)}")
    return "\n".join(lines)


async def _execute_tool(
    action: str,
    action_input: dict[str, Any],
    state: VulcanOpsState,
) -> str:
    if action == "retrieve_more":
        query = action_input.get("query", "")
        return await _retrieve_more(query, state)
    if action == "get_sensor_history":
        sensor = action_input.get("sensor", "")
        hours = int(action_input.get("hours", 24))
        return await _get_sensor_history(sensor, hours, state)
    if action == "search_maintenance":
        failure_mode = action_input.get("failure_mode", "")
        return await _search_maintenance(failure_mode, state)
    return f"Unknown action: {action}"


_EMPTY_OBSERVATION_RE = re.compile(
    r"^\s*$|"
    r"\bno\s+\w+\s+(readings|records|results|matches|data|evidence)\b|"
    r"\bno\s+maintenance\s+records\b|"
    r"\bno\s+additional\s+documents\b",
    re.IGNORECASE,
)


def _is_empty_observation(observation: str) -> bool:
    return bool(_EMPTY_OBSERVATION_RE.search(observation))


def _fallback_result(error: LLMError) -> AgentResult:
    """Deterministic fallback used when the LLM is unavailable."""
    trace = [
        ReActStep(
            iteration=1,
            thought=f"LLM unavailable ({type(error).__name__}); falling back to deterministic thresholds.",
            action="conclude",
            action_input={
                "failure_mode": "insufficient evidence",
                "root_cause": "manual inspection required",
                "confidence": 0.2,
                "reasoning": "LLM service unavailable. Analysis based on deterministic sensor thresholds only.",
            },
            observation="Concluded: insufficient evidence (confidence=0.2)",
        ),
    ]
    diagnosis = DiagnosisResult(
        root_cause="manual inspection required",
        failure_mode="insufficient evidence",
        confidence=0.2,
        supporting_evidence=[],
        reasoning_trace=trace,
    )
    print(
        f"[diagnosis_agent] LLM unavailable, deterministic fallback ({type(error).__name__})",
        flush=True,
    )
    return AgentResult(
        status="success",
        data={
            "root_cause": diagnosis.root_cause,
            "failure_mode": diagnosis.failure_mode,
            "reasoning": "LLM service unavailable. Analysis based on deterministic sensor thresholds only.",
            "confidence": diagnosis.confidence,
            "evidence_used": [],
            "reasoning_trace": [step.model_dump() for step in trace],
            "llm_telemetry": {
                "model": None,
                "calls": [],
                "iterations": 1,
                "fallback_used": True,
                "error": type(error).__name__,
            },
        },
    )


def _make_diagnosis_data(action_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize the conclude tool arguments into a diagnosis dict."""
    return {
        "root_cause": action_input.get("root_cause", "manual inspection required"),
        "failure_mode": action_input.get("failure_mode", "unspecified"),
        "confidence": float(action_input.get("confidence", 0.2)),
        "reasoning": action_input.get("reasoning", "No reasoning provided by model."),
        "evidence_used": [],
    }


async def run(state: VulcanOpsState) -> AgentResult:
    if not state.sensor_readings and not state.anomaly:
        return AgentResult(
            status="error",
            data={},
            errors=["Diagnosis requires at least sensor_readings or anomaly data"],
        )

    trace: list[ReActStep] = []
    used_tool_keys: set[str] = set()
    diagnosis_data: dict[str, Any] | None = None
    telemetry_calls: list[dict[str, Any]] = []

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_initial_observation(state)},
    ]
    empty_streak = 0

    for iteration in range(1, _MAX_ITERATIONS + 1):
        try:
            result = await llm_service.call_with_tools(
                agent="diagnosis_agent",
                system=_REACT_SYSTEM_PROMPT,
                messages=messages,
                tools=_TOOLS,
            )
        except LLMError as exc:
            return _fallback_result(exc)

        telemetry_calls.append({"iteration": iteration, "kind": result.kind})

        thought = result.content or "(no narration)"

        if result.kind == "final":
            # Model refused to call a tool — treat as low-confidence conclude.
            diagnosis_data = {
                "root_cause": "manual inspection required",
                "failure_mode": "insufficient evidence",
                "confidence": 0.3,
                "reasoning": f"Model returned final text instead of a tool call: {result.content}",
                "evidence_used": [],
            }
            trace.append(
                ReActStep(
                    iteration=iteration,
                    thought=thought,
                    action="conclude",
                    action_input={
                        "failure_mode": diagnosis_data["failure_mode"],
                        "root_cause": diagnosis_data["root_cause"],
                        "confidence": diagnosis_data["confidence"],
                        "reasoning": diagnosis_data["reasoning"],
                    },
                    observation="Concluded via final-text fallback.",
                )
            )
            print(
                f"[diagnosis_agent] iteration={iteration} action=conclude (final-text fallback)",
                flush=True,
            )
            break

        action = result.tool_name or ""
        action_input = result.tool_args or {}
        tool_call_id = result.tool_call_id or f"synthetic-{iteration}"

        if action == "conclude":
            diagnosis_data = _make_diagnosis_data(action_input)
            observation = (
                f"Concluded: {diagnosis_data.get('failure_mode', 'unknown')} "
                f"(confidence={diagnosis_data.get('confidence', 0.0)})"
            )
            trace.append(
                ReActStep(
                    iteration=iteration,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    observation=observation,
                )
            )
            print(
                f"[diagnosis_agent] iteration={iteration} action=conclude "
                f"thought={thought[:120]!r} observation={observation[:120]!r}",
                flush=True,
            )
            break

        tool_key = f"{action}:{json.dumps(action_input, sort_keys=True)}"
        is_repeat = tool_key in used_tool_keys
        if is_repeat:
            observation = (
                f"You already called '{action}' with {json.dumps(action_input)}. "
                "You must not repeat the same tool call. Choose a different tool or call 'conclude'."
            )
        else:
            used_tool_keys.add(tool_key)
            try:
                observation = await _execute_tool(action, action_input, state)
            except Exception as exc:
                observation = f"Tool {action} failed: {exc}"

        print(
            f"[diagnosis_agent] iteration={iteration} action={action} "
            f"input={json.dumps(action_input)} observation={observation[:120]!r}",
            flush=True,
        )

        trace.append(
            ReActStep(
                iteration=iteration,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation,
            )
        )

        # Append the assistant message that contains the tool call so the model sees its own history.
        assistant_tool_call = {
            "id": tool_call_id,
            "type": "function",
            "function": {"name": action, "arguments": json.dumps(action_input)},
        }
        messages.append(
            {
                "role": "assistant",
                "content": thought if thought != "(no narration)" else "",
                "tool_calls": [assistant_tool_call],
            }
        )
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": observation})

        if is_repeat:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That was a repeated tool call. Do not call it again. "
                        "Pick a different tool or call 'conclude' with your best diagnosis."
                    ),
                }
            )
            continue

        if _is_empty_observation(observation):
            empty_streak += 1
            if empty_streak >= 2:
                messages.append(
                    {
                        "role": "user",
                        "content": "No more useful evidence is available. Call 'conclude' now.",
                    }
                )
                continue
        else:
            empty_streak = 0

    if diagnosis_data is None:
        # Loop exhausted without a conclude — use the best guess we have.
        diagnosis_data = {
            "root_cause": "manual inspection required",
            "failure_mode": "insufficient evidence",
            "confidence": 0.3,
            "reasoning": "ReAct loop exhausted without a conclusion. Preliminary hypothesis could not be confirmed.",
            "evidence_used": [],
        }

    # Ensure the trace always ends with a conclude step representing the final diagnosis.
    if not trace or trace[-1].action != "conclude":
        final_iteration = trace[-1].iteration + 1 if trace else 1
        trace.append(
            ReActStep(
                iteration=final_iteration,
                thought=diagnosis_data.get("reasoning", "Final diagnosis reached."),
                action="conclude",
                action_input={
                    "failure_mode": diagnosis_data.get("failure_mode"),
                    "root_cause": diagnosis_data.get("root_cause"),
                    "confidence": diagnosis_data.get("confidence"),
                },
                observation=(
                    f"Concluded: {diagnosis_data.get('failure_mode', 'unknown')} "
                    f"(confidence={diagnosis_data.get('confidence', 0.0)})"
                ),
            )
        )

    merged_telemetry: dict[str, Any] = {
        "model": None,
        "calls": telemetry_calls,
        "iterations": len(trace),
    }

    diagnosis = DiagnosisResult(
        root_cause=diagnosis_data.get("root_cause"),
        failure_mode=diagnosis_data.get("failure_mode"),
        confidence=diagnosis_data.get("confidence"),
        supporting_evidence=diagnosis_data.get("evidence_used", []),
        reasoning_trace=trace,
    )

    return AgentResult(
        status="success",
        data={
            "root_cause": diagnosis.root_cause,
            "failure_mode": diagnosis.failure_mode,
            "reasoning": diagnosis_data.get("reasoning", ""),
            "confidence": diagnosis.confidence,
            "evidence_used": diagnosis.supporting_evidence,
            "reasoning_trace": [step.model_dump() for step in trace],
            "llm_telemetry": merged_telemetry,
        },
    )
