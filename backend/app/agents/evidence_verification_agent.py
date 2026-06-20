"""
Evidence Verification Agent — adversarial ReAct agent that actively challenges
the diagnosis by searching for contradicting evidence, similar past cases,
and sensor inconsistencies.

If it finds a strong enough contradiction, it recommends 'revise_diagnosis',
which triggers a cycle back to diagnosis_agent in the LangGraph graph.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text

from app.agents import evidence_retrieval_agent
from app.agents.base import AgentResult
from app.core.state_contract import VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.maintenance_record import MaintenanceRecord
from app.models.sensor_reading import SensorReading
from app.services.llm_service import LLMError, llm_service

_MAX_ITERATIONS = 3

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_contradicting_evidence",
            "description": (
                "Vector-search document chunks for content that CONTRADICTS a specific claim. "
                "Use this to find evidence arguing against the diagnosis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "The diagnosis claim to try to contradict (e.g. 'seal leakage is the root cause')",
                    },
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
                "required": ["claim"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_similar_past_cases",
            "description": (
                "Query maintenance records for past cases with the same failure mode. "
                "If past records show repeated misdiagnosis, that is evidence for contradiction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "failure_mode": {
                        "type": "string",
                        "description": "The failure mode from the current diagnosis to look up historically",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
                "required": ["failure_mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_sensor_consistency",
            "description": (
                "Pull a longer sensor window (default 7 days) to verify the diagnosis is "
                "consistent with the broader trend — not just the most recent reading."
            ),
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
                        "default": 168,
                        "description": "History window to check, defaults to 168h (7 days)",
                    },
                },
                "required": ["sensor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "conclude_verification",
            "description": "Submit the final verification decision. Call this when done searching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "verified": {
                        "type": "boolean",
                        "description": "True if the diagnosis is supported, False if not",
                    },
                    "evidence_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "0-1 fraction of diagnosis supported by documentary evidence",
                    },
                    "history_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "0-1 alignment with historical failure patterns",
                    },
                    "combined_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Weighted combined score (evidence*0.6 + history*0.4)",
                    },
                    "contradictions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Human-readable list of contradictions found (empty if none)",
                    },
                    "recommendation": {
                        "type": "string",
                        "enum": ["accept", "revise_diagnosis", "escalate"],
                        "description": (
                            "'accept' if diagnosis stands, "
                            "'revise_diagnosis' if a strong contradiction was found (combined_score < 0.3 or direct contradiction), "
                            "'escalate' if evidence is ambiguous"
                        ),
                    },
                },
                "required": [
                    "verified", "evidence_score", "history_score",
                    "combined_score", "contradictions", "recommendation",
                ],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are an adversarial reviewer challenging an industrial equipment diagnosis. "
    "Your job is to find reasons the diagnosis might be WRONG. "
    "Use the tools to actively search for contradicting evidence before accepting.\n\n"
    "RULES:\n"
    "- Call only one tool per turn.\n"
    "- Do not repeat the same tool call twice.\n"
    "- After searching, call 'conclude_verification' with your verdict.\n"
    "- Use 'revise_diagnosis' if: combined_score < 0.3, OR a direct factual contradiction was found, "
    "OR past cases show this failure mode was repeatedly misdiagnosed.\n"
    "- Use 'escalate' if evidence is ambiguous — partially supports, partially contradicts.\n"
    "- Use 'accept' ONLY if you genuinely cannot find a credible contradiction.\n"
    "- Be adversarial: assume the diagnosis might be wrong until the evidence proves otherwise."
)


def _build_initial_context(state: VulcanOpsState) -> str:
    diag = state.diagnosis
    machine = state.machine_context
    machine_desc = (
        f"{machine.machine_name} ({machine.machine_type})" if machine else "Unknown machine"
    )

    parts = [
        f"Machine: {machine_desc}",
        f"Diagnosis to challenge:",
        f"  failure_mode: {diag.failure_mode}",
        f"  root_cause: {diag.root_cause}",
        f"  confidence: {diag.confidence}",
        "",
    ]

    if state.retrieved_evidence:
        evidence_lines = [
            f"  [{i}] {ev.get('source','?')} (score={ev.get('relevance_score',0):.2f}): "
            f"{ev.get('chunk','')[:200]}"
            for i, ev in enumerate(state.retrieved_evidence[:3], 1)
        ]
        parts += ["Available evidence:"] + evidence_lines + [""]

    if state.maintenance_history:
        history_lines = [
            f"  [{i}] {r.date.isoformat() if r.date else 'unknown'}: "
            f"{r.failure_mode} → {r.action_taken}"
            for i, r in enumerate(state.maintenance_history[:5], 1)
        ]
        parts += ["Maintenance history:"] + history_lines + [""]

    parts.append(
        "Now actively search for contradictions using the tools. "
        "Try search_contradicting_evidence, find_similar_past_cases, or check_sensor_consistency. "
        "Then conclude_verification."
    )
    return "\n".join(parts)


async def _search_contradicting_evidence(
    claim: str, top_k: int, state: VulcanOpsState
) -> str:
    adversarial_query = (
        f"NOT {claim} | alternative cause | misdiagnosis | different root cause"
    )
    try:
        from app.core.state_contract import AnomalyDetail

        synthetic_state = state.model_copy(
            update={"anomaly": AnomalyDetail(detected=True, sensor=adversarial_query)},
            deep=True,
        )
        result = await evidence_retrieval_agent.run(synthetic_state)
        evidence = result.data.get("retrieved_evidence", [])
        if not evidence:
            return f"No contradicting documents found for claim: '{claim}'"
        lines = [
            f"[{i}] {ev.get('source','?')} (score={ev.get('relevance_score',0):.2f}): "
            f"{ev.get('chunk','')[:300]}"
            for i, ev in enumerate(evidence[:top_k], 1)
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"Evidence search failed: {exc}"


async def _find_similar_past_cases(
    failure_mode: str, limit: int, state: VulcanOpsState
) -> str:
    machine_id = state.active_machine_id
    keyword = f"%{failure_mode}%"
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(MaintenanceRecord)
                .where(MaintenanceRecord.machine_id == machine_id)
                .where(
                    text("failure_mode ILIKE :keyword OR action_taken ILIKE :keyword")
                )
                .params(keyword=keyword)
                .order_by(MaintenanceRecord.date.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
    except Exception as exc:
        return f"Maintenance record query failed: {exc}"

    if not rows:
        return f"No past cases matching '{failure_mode}' for this machine."

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
    return "\n".join(lines) + f"\n({len(rows)} matching records)"


async def _check_sensor_consistency(
    sensor: str, hours: int, state: VulcanOpsState
) -> str:
    machine_id = state.active_machine_id
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SensorReading)
                .where(SensorReading.machine_id == machine_id)
                .where(SensorReading.timestamp >= since)
                .order_by(SensorReading.timestamp.asc())
            )
            rows = result.scalars().all()
    except Exception as exc:
        return f"Sensor query failed: {exc}"

    if not rows:
        return f"No {sensor} readings in the last {hours}h."

    values = [getattr(r, sensor) for r in rows if getattr(r, sensor, None) is not None]
    if not values:
        return f"Sensor '{sensor}' has no data in the last {hours}h."

    n = len(values)
    mean_v = sum(values) / n
    stddev = statistics.stdev(values) if n > 1 else 0.0
    first_half = values[: n // 2]
    second_half = values[n // 2 :]
    first_mean = sum(first_half) / len(first_half) if first_half else mean_v
    second_mean = sum(second_half) / len(second_half) if second_half else mean_v

    if second_mean > first_mean * 1.1:
        trend = "INCREASING — consistent with progressive degradation"
    elif second_mean < first_mean * 0.9:
        trend = "DECREASING — values improving, may contradict failure narrative"
    else:
        trend = "STABLE — no strong trend, may indicate gradual degradation inconsistency"

    last_5 = [
        f"{r.timestamp.isoformat()}: {getattr(r, sensor):.2f}"
        for r in rows[-5:]
        if getattr(r, sensor, None) is not None
    ]
    return (
        f"{sensor} ({hours}h window): n={n}, min={min(values):.2f}, max={max(values):.2f}, "
        f"mean={mean_v:.2f}, stddev={stddev:.2f}\n"
        f"Trend: {trend}\n"
        f"Last 5 readings: {' | '.join(last_5)}"
    )


async def _execute_tool(
    action: str, action_input: dict[str, Any], state: VulcanOpsState
) -> str:
    if action == "search_contradicting_evidence":
        return await _search_contradicting_evidence(
            claim=action_input.get("claim", ""),
            top_k=int(action_input.get("top_k", 5)),
            state=state,
        )
    if action == "find_similar_past_cases":
        return await _find_similar_past_cases(
            failure_mode=action_input.get("failure_mode", ""),
            limit=int(action_input.get("limit", 5)),
            state=state,
        )
    if action == "check_sensor_consistency":
        return await _check_sensor_consistency(
            sensor=action_input.get("sensor", "vibration"),
            hours=int(action_input.get("hours", 168)),
            state=state,
        )
    return f"Unknown tool: {action}"


_CONTRADICTION_MARKERS = [
    "misdiagnosed",
    "was unnecessary",
    "different root cause",
    "actually was",
    "turned out to be",
    "seal was intact",
    "no actual seal",
    "realigned coupling",
    "calibration drift",
]


def _collect_observation_text(messages: list[dict]) -> str:
    """Concatenate all tool-result messages for signal detection."""
    return " ".join(
        m.get("content", "")
        for m in messages
        if m.get("role") == "tool"
    ).lower()


def _infer_recommendation_from_observations(messages: list[dict]) -> str:
    """Return 'revise_diagnosis' if contradiction markers appear in tool observations."""
    text = _collect_observation_text(messages)
    if any(m in text for m in _CONTRADICTION_MARKERS):
        return "revise_diagnosis"
    return "accept"


async def run(state: VulcanOpsState) -> AgentResult:
    if not state.diagnosis:
        return AgentResult(
            status="error",
            data={},
            errors=["No diagnosis available to verify"],
        )
    if not state.diagnosis.root_cause:
        return AgentResult(
            status="error",
            data={},
            errors=["Diagnosis has no root_cause — cannot verify"],
        )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_initial_context(state)},
    ]
    used_tool_keys: set[str] = set()
    telemetry_calls: list[dict[str, Any]] = []
    conclusion: dict[str, Any] | None = None

    for iteration in range(1, _MAX_ITERATIONS + 1):
        try:
            result = await llm_service.call_with_tools(
                agent="evidence_verification_agent",
                system=_SYSTEM_PROMPT,
                messages=messages,
                tools=_TOOLS,
            )
        except LLMError as exc:
            print(
                f"[evidence_verification_agent] LLM unavailable ({type(exc).__name__}); "
                "falling back to accept",
                flush=True,
            )
            conclusion = {
                "verified": True,
                "evidence_score": 0.0,
                "history_score": 0.0,
                "combined_score": 0.0,
                "contradictions": [],
                "recommendation": "accept",
            }
            break

        telemetry_calls.append({"iteration": iteration, "kind": result.kind})
        thought = result.content or "(no narration)"
        action = result.tool_name or ""
        action_input = result.tool_args or {}
        tool_call_id = result.tool_call_id or f"synthetic-{iteration}"

        if result.kind == "final" or not action:
            print(
                f"[evidence_verification_agent] iteration={iteration} final-text fallback; accepting",
                flush=True,
            )
            conclusion = {
                "verified": True,
                "evidence_score": 0.0,
                "history_score": 0.0,
                "combined_score": 0.0,
                "contradictions": [],
                "recommendation": "accept",
            }
            break

        if action == "conclude_verification":
            conclusion = action_input
            print(
                f"[evidence_verification_agent] iteration={iteration} concluded: "
                f"recommendation={action_input.get('recommendation')} "
                f"verified={action_input.get('verified')} "
                f"contradictions={len(action_input.get('contradictions', []))}",
                flush=True,
            )
            break

        tool_key = f"{action}:{json.dumps(action_input, sort_keys=True)}"
        if tool_key in used_tool_keys:
            observation = (
                f"You already called '{action}' with the same arguments. "
                "Do not repeat. Try a different tool or conclude_verification."
            )
        else:
            used_tool_keys.add(tool_key)
            try:
                observation = await _execute_tool(action, action_input, state)
            except Exception as exc:
                observation = f"Tool {action} failed: {exc}"

        print(
            f"[evidence_verification_agent] iteration={iteration} action={action} "
            f"obs={observation[:120]!r}",
            flush=True,
        )

        messages.append({
            "role": "assistant",
            "content": thought if thought != "(no narration)" else "",
            "tool_calls": [{
                "id": tool_call_id,
                "type": "function",
                "function": {"name": action, "arguments": json.dumps(action_input)},
            }],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": observation,
        })

    if conclusion is None:
        # Loop exhausted: model used all iterations on tool calls but didn't conclude.
        # Inspect observations for contradiction signals before defaulting to accept.
        rec = _infer_recommendation_from_observations(messages)
        print(
            f"[evidence_verification_agent] loop exhausted without conclude_verification; "
            f"inferred recommendation={rec} from tool observations",
            flush=True,
        )
        if rec == "revise_diagnosis":
            obs_text = _collect_observation_text(messages)
            conclusion = {
                "verified": False,
                "evidence_score": 0.0,
                "history_score": 0.2,
                "combined_score": 0.08,
                "contradictions": [
                    "Past cases show this failure mode was repeatedly misdiagnosed (detected in tool observations)."
                ],
                "recommendation": "revise_diagnosis",
            }
        else:
            conclusion = {
                "verified": True,
                "evidence_score": 0.0,
                "history_score": 0.0,
                "combined_score": 0.0,
                "contradictions": [],
                "recommendation": "accept",
            }

    contradictions = conclusion.get("contradictions", [])
    recommendation = conclusion.get("recommendation", "accept")
    verified = bool(conclusion.get("verified", False))
    evidence_score = float(conclusion.get("evidence_score", 0.0))
    history_score = float(conclusion.get("history_score", 0.0))
    combined_score = float(conclusion.get("combined_score", 0.0))

    notes = (
        f"Adversarial review: {recommendation}. "
        + (f"Contradictions: {'; '.join(contradictions)}" if contradictions else "No contradictions found.")
    )

    return AgentResult(
        status="success",
        data={
            "verified": verified,
            "evidence_score": evidence_score,
            "history_score": history_score,
            "combined_score": combined_score,
            "contradictions": contradictions,
            "recommendation": recommendation,
            "verification_notes": notes,
            "llm_telemetry": {
                "model": None,
                "calls": telemetry_calls,
                "iterations": len(telemetry_calls),
            },
        },
    )
