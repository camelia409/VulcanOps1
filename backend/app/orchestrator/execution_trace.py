"""
Execution trace utilities for the VulcanOps agent pipeline.

Each agent execution is recorded as:
{
    "agent_name":   str,
    "start_time":   str,     # ISO 8601 UTC
    "end_time":     str,     # ISO 8601 UTC
    "latency_ms":   float,
    "status":       str,     # "success" | "error" | "skipped" | "partial"
    "llm_called":   bool,    # True only for diagnosis_agent, evidence_verification_agent, maintenance_strategy_agent, supervisor_planner, communication_formatter
    "cache_hit":    bool,    # True when the LLM call was served from in-process cache
}

Traces are stored in state.execution_trace[] and returned with the final state.
The llm_called / cache_hit fields allow the Reports viewer and debug scripts to
identify which agents consumed LLM budget.
"""

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_trace(
    agent_name: str,
    start: datetime,
    end: datetime,
    status: str,
    llm_called: bool = False,
    cache_hit: bool = False,
    degraded_reason: str | None = None,
) -> dict:
    latency_ms = (end - start).total_seconds() * 1000
    entry: dict = {
        "agent_name": agent_name,
        "start_time": start.isoformat(),
        "end_time":   end.isoformat(),
        "latency_ms": round(latency_ms, 1),
        "status":     status,
        "llm_called": llm_called,
        "cache_hit":  cache_hit,
    }
    if degraded_reason is not None:
        entry["degraded_reason"] = degraded_reason
    return entry


def skipped_trace(agent_name: str, reason: str) -> dict:
    """Record an agent that was skipped due to an invariant."""
    ts = now_utc().isoformat()
    return {
        "agent_name":  agent_name,
        "start_time":  ts,
        "end_time":    ts,
        "latency_ms":  0.0,
        "status":      "skipped",
        "skip_reason": reason,
        "llm_called":  False,
        "cache_hit":   False,
    }
