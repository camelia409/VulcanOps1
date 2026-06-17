"""
Integration Service — Industrial Copilot execution layer.

Single public entry point:
    result = await execute_from_intent(routing, query, db, session_context)

Architecture:
    ┌─────────────┐
    │  chat.py    │  HTTP adapter
    └──────┬──────┘
           ↓
    ┌─────────────────────┐
    │  query_router.py    │  Deterministic intent classification (no LLM)
    └──────┬──────────────┘
           ↓
    ┌─────────────────────────────────────────────────────┐
    │  integration_service.py  (this file)                │
    │                                                      │
    │  1. Resolve session memory (last_machine_id)         │
    │  2. Query report_batches / machines from DB          │
    │  3. Check in-memory response cache                   │
    │  4. Optionally call LLM for copilot_answer (light)   │
    │  5. Return structured response                       │
    │                                                      │
    │  READ-ONLY — never calls run_pipeline()              │
    └─────────────────────────────────────────────────────┘

Intents handled (all read-only):
    plant_summary           — Plant-wide overview from all latest batches
    highest_risk            — Machine with highest risk from cached reports
    top_priority            — Top 3 machines by priority / risk
    emergency_machines      — Machines with priority = Emergency
    low_confidence_machines — Machines with diagnosis confidence < 0.7
    rul_query               — Machine(s) by remaining useful life
    investigate_machine     — Single machine deep report from cache
    prioritize_today        — Top machines by criticality
    critical_machines       — CRITICAL criticality machines (list only)
    daily_report            — Top-3 machines summary
"""

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MachineCriticality, MachineStatus
from app.models.machine import Machine
from app.models.report_batch import ReportBatch as ReportBatchModel
from app.services import report_builder
from app.services.llm_service import llm_service
from app.services.query_router import RoutingResult

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────

_MAX_MACHINES_PER_REQUEST = 3

_CRITICALITY_RANK = case(
    (Machine.criticality == MachineCriticality.CRITICAL, 1),
    (Machine.criticality == MachineCriticality.HIGH, 2),
    (Machine.criticality == MachineCriticality.MEDIUM, 3),
    (Machine.criticality == MachineCriticality.LOW, 4),
    else_=5,
)

_STATUS_RANK = case(
    (Machine.status == MachineStatus.DEGRADED, 1),
    (Machine.status == MachineStatus.OPERATIONAL, 2),
    (Machine.status == MachineStatus.UNDER_MAINTENANCE, 3),
    (Machine.status == MachineStatus.OFFLINE, 4),
    else_=5,
)

_PRIORITY_RANK = case(
    (func.lower(ReportBatchModel.priority) == "emergency", 1),
    (func.lower(ReportBatchModel.priority) == "urgent", 2),
    (func.lower(ReportBatchModel.priority) == "routine", 3),
    else_=4,
)

# ── in-memory LLM answer cache ────────────────────────────────────────────────
# Key: md5(machine_id + root_cause + query_normalized)
# Eviction: clear when > 200 entries (simple, process-scoped)

_COPILOT_CACHE: dict[str, str] = {}
_CACHE_MAX = 200


def _copilot_cache_key(machine_id: str, root_cause: str | None, query: str) -> str:
    raw = f"{machine_id}:{root_cause or ''}:{query.lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── machine selection helpers ─────────────────────────────────────────────────


async def _top_machines(db: AsyncSession, limit: int) -> list[Machine]:
    result = await db.execute(
        select(Machine)
        .where(Machine.status.notin_([MachineStatus.DECOMMISSIONED]))
        .order_by(_CRITICALITY_RANK, _STATUS_RANK)
        .limit(limit)
    )
    return list(result.scalars().all())


async def _machines_by_type(db: AsyncSession, machine_type: str) -> list[Machine]:
    result = await db.execute(
        select(Machine)
        .where(Machine.machine_type.ilike(f"%{machine_type}%"))
        .where(Machine.status.notin_([MachineStatus.DECOMMISSIONED]))
        .order_by(_CRITICALITY_RANK, _STATUS_RANK)
        .limit(_MAX_MACHINES_PER_REQUEST)
    )
    return list(result.scalars().all())


async def _critical_machines(db: AsyncSession) -> list[Machine]:
    result = await db.execute(
        select(Machine)
        .where(Machine.criticality == MachineCriticality.CRITICAL)
        .where(Machine.status.notin_([MachineStatus.DECOMMISSIONED]))
        .order_by(_STATUS_RANK)
    )
    return list(result.scalars().all())


def _machine_to_dict(m: Machine) -> dict[str, Any]:
    return {
        "machine_id": str(m.machine_id),
        "machine_name": m.machine_name,
        "machine_type": m.machine_type,
        "plant": m.plant,
        "location": m.location,
        "criticality": m.criticality.value,
        "status": m.status.value,
    }


# ── latest-batch-per-machine subquery ─────────────────────────────────────────


def _latest_batch_subquery():
    """Subquery: latest generated_at per machine_id."""
    return (
        select(
            ReportBatchModel.machine_id,
            func.max(ReportBatchModel.generated_at).label("max_at"),
        )
        .group_by(ReportBatchModel.machine_id)
        .subquery()
    )


async def _all_latest_batches(db: AsyncSession) -> list[ReportBatchModel]:
    """One batch per machine — the most recently generated."""
    subq = _latest_batch_subquery()
    result = await db.execute(
        select(ReportBatchModel).join(
            subq,
            (ReportBatchModel.machine_id == subq.c.machine_id)
            & (ReportBatchModel.generated_at == subq.c.max_at),
        )
    )
    return list(result.scalars().all())


# ── cache helpers ─────────────────────────────────────────────────────────────


async def _get_cached_report(machine_id: uuid.UUID, db: AsyncSession) -> dict[str, Any] | None:
    result = await db.execute(
        select(ReportBatchModel)
        .where(ReportBatchModel.machine_id == machine_id)
        .order_by(ReportBatchModel.generated_at.desc())
        .limit(1)
    )
    batch = result.scalar_one_or_none()
    return batch.full_report_json if batch else None


async def _get_cached_report_by_id(machine_id_str: str, db: AsyncSession) -> dict[str, Any] | None:
    try:
        mid = uuid.UUID(machine_id_str)
    except ValueError:
        return None
    return await _get_cached_report(mid, db)


# ── report resolution (READ-ONLY — never re-runs pipeline) ───────────────────


async def _resolve_report(m: Machine, db: AsyncSession) -> dict[str, Any]:
    """
    Return cached report for this machine.
    If no cache: returns a structured placeholder — does NOT call run_pipeline().
    Chat is read-only. Agents only run during ingestion.
    """
    cached = await _get_cached_report(m.machine_id, db)
    if cached is not None:
        return cached

    return {
        "machine": _machine_to_dict(m),
        "no_cache": True,
        "has_errors": False,
        "root_cause": None,
        "failure_mode": None,
        "diagnosis_confidence": None,
        "risk_level": None,
        "recommended_action": "Ingest data first — no report exists for this machine yet.",
        "priority": None,
        "rul_hours": None,
        "estimated_downtime_hours": None,
        "estimated_cost_usd": None,
        "parts_required": [],
        "anomaly": None,
        "verification": None,
        "engineer_report": None,
        "supervisor_report": None,
        "manager_report": None,
        "execution_trace": [],
        "pipeline_errors": 0,
    }


# ── LLM copilot answer (lightweight, cached) ─────────────────────────────────


async def _get_copilot_answer(report: dict[str, Any], query: str) -> str | None:
    """
    Generate a short LLM summary answering the user's specific question.
    Uses in-memory cache: same machine + same root_cause + same query = free hit.
    Returns None if report has no cache (machine not yet analysed).
    """
    if report.get("no_cache"):
        return None

    machine = report.get("machine") or {}
    machine_id = machine.get("machine_id", "")
    root_cause = report.get("root_cause") or ""

    cache_key = _copilot_cache_key(machine_id, root_cause, query)
    if cache_key in _COPILOT_CACHE:
        return _COPILOT_CACHE[cache_key]

    facts_lines = [
        f"Machine: {machine.get('machine_name', 'Unknown')}",
        f"Type: {machine.get('machine_type', 'Unknown')}",
        f"Priority: {report.get('priority', 'Unknown')}",
        f"Risk Level: {report.get('risk_level', 'Unknown')}",
        f"RUL: {report.get('rul_hours', 'Unknown')} hours",
        f"Diagnosis Confidence: {report.get('diagnosis_confidence', 'Unknown')}",
        f"Root Cause: {report.get('root_cause', 'Unknown')}",
        f"Failure Mode: {report.get('failure_mode', 'Unknown')}",
        f"Recommended Action: {report.get('recommended_action', 'Unknown')}",
        f"Analysis Type: {report.get('deep_analysis_status', 'done')}",
    ]

    # Explainability facts — let the copilot answer "Why?" / "Show evidence" questions
    explainability_score = report.get("explainability_score")
    if explainability_score is not None:
        facts_lines.append(f"Explainability Score: {explainability_score}/100")

    evidence_chain = report.get("evidence_chain") or []
    if evidence_chain:
        facts_lines.append("Evidence Chain:")
        for item in evidence_chain:
            facts_lines.append(
                f"  Step {item.get('step')}: [{item.get('type')}] {item.get('source')} — {item.get('evidence')}"
            )

    # Procurement gap facts — let the copilot answer "Is procurement at risk?"
    procurement_gap = report.get("procurement_gap") or {}
    if procurement_gap.get("procurement_gap"):
        facts_lines.append("Procurement Gap: Detected")
        facts_lines.append(
            f"Procurement Action: {procurement_gap.get('recommended_action', 'Expedite parts procurement')}"
        )
        for at_risk in procurement_gap.get("at_risk_parts", []):
            facts_lines.append(
                f"  At-risk part: {at_risk.get('part')} (lead {at_risk.get('lead_time_days')} days, RUL {at_risk.get('rul_days')} days)"
            )
    else:
        facts_lines.append("Procurement Gap: No detected lead-time risk")

    facts = "\n".join(facts_lines)

    try:
        answer = await llm_service.generate_copilot_answer(facts, query)
    except Exception as exc:
        logger.warning("Copilot LLM answer failed: %s", exc)
        return None

    if len(_COPILOT_CACHE) >= _CACHE_MAX:
        _COPILOT_CACHE.clear()
    _COPILOT_CACHE[cache_key] = answer

    return answer


# ── plant overview (public, used by /chat/plant-overview endpoint) ────────────


async def get_plant_overview(db: AsyncSession) -> dict[str, Any]:
    """
    Aggregate statistics across all latest cached reports.
    No LLM, no pipeline — pure DB read.
    """
    batches = await _all_latest_batches(db)

    if not batches:
        return {
            "total_machines": 0,
            "emergency_count": 0,
            "urgent_count": 0,
            "routine_count": 0,
            "full_ai_count": 0,
            "fast_count": 0,
            "error_count": 0,
            "last_processed": None,
        }

    def pri(b: ReportBatchModel) -> str:
        return (b.priority or "").lower()

    emergency = sum(1 for b in batches if pri(b) == "emergency")
    urgent = sum(1 for b in batches if pri(b) == "urgent")
    routine = sum(1 for b in batches if pri(b) == "routine")

    full_ai = sum(
        1 for b in batches
        if (b.full_report_json or {}).get("deep_analysis_status", "done") != "queued"
    )
    fast = sum(
        1 for b in batches
        if (b.full_report_json or {}).get("deep_analysis_status") == "queued"
    )
    errors = sum(1 for b in batches if (b.pipeline_errors or 0) > 0)

    last_processed = max(
        (b.generated_at for b in batches if b.generated_at),
        default=None,
    )

    return {
        "total_machines": len(batches),
        "emergency_count": emergency,
        "urgent_count": urgent,
        "routine_count": routine,
        "full_ai_count": full_ai,
        "fast_count": fast,
        "error_count": errors,
        "last_processed": last_processed.isoformat() if last_processed else None,
    }


# ── session memory resolution ─────────────────────────────────────────────────


async def _resolve_machine_from_context(
    machine_id_str: str,
    db: AsyncSession,
) -> tuple[Machine | None, dict[str, Any] | None]:
    """
    Resolve a machine UUID from session context.
    Returns (Machine ORM object, cached_report_dict) or (None, None).
    """
    try:
        mid = uuid.UUID(machine_id_str)
    except ValueError:
        return None, None

    result = await db.execute(select(Machine).where(Machine.machine_id == mid))
    machine = result.scalar_one_or_none()
    if machine is None:
        return None, None

    report = await _get_cached_report(mid, db)
    return machine, report


# ── base response builder ─────────────────────────────────────────────────────


def _base_response(
    title: str,
    intent: str,
    query: str,
    confidence: float,
    reports: list[dict[str, Any]] | None = None,
    machines: list[dict[str, Any]] | None = None,
    plant_overview: dict[str, Any] | None = None,
    copilot_answer: str | None = None,
    cache_hit: bool = True,
) -> dict[str, Any]:
    reps = reports or []
    return {
        "title": title,
        "intent": intent,
        "query": query,
        "routing_confidence": confidence,
        "reports": reps,
        "machines": machines,
        "report_count": len(reps),
        "plant_overview": plant_overview,
        "copilot_answer": copilot_answer,
        "cache_hit": cache_hit,
    }


# ── intent handlers ───────────────────────────────────────────────────────────


async def _execute_plant_summary(
    routing: RoutingResult, query: str, db: AsyncSession, _ctx: dict
) -> dict[str, Any]:
    overview = await get_plant_overview(db)
    return _base_response(
        title="Plant Overview",
        intent=routing.intent,
        query=query,
        confidence=routing.confidence,
        plant_overview=overview,
    )


async def _execute_highest_risk(
    routing: RoutingResult, query: str, db: AsyncSession, _ctx: dict
) -> dict[str, Any]:
    # Find machine with highest risk score from cached reports
    batches = await _all_latest_batches(db)
    if not batches:
        return _base_response(
            title="Highest Risk Machine — No Data",
            intent=routing.intent, query=query, confidence=routing.confidence,
        )

    def risk_key(b: ReportBatchModel) -> float:
        rj = b.full_report_json or {}
        rs = rj.get("risk_score")
        if rs is not None:
            try:
                return float(rs)
            except (TypeError, ValueError):
                pass
        pri = (b.priority or "").lower()
        return {"emergency": 100.0, "urgent": 70.0, "routine": 30.0}.get(pri, 0.0)

    top = max(batches, key=risk_key)
    report = top.full_report_json or {}
    machine_name = (top.machine.machine_name if top.machine else None) or str(top.machine_id)[:8]

    copilot_answer = await _get_copilot_answer(report, query)
    return _base_response(
        title=f"Highest Risk: {machine_name}",
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=[report], copilot_answer=copilot_answer,
    )


async def _execute_top_priority(
    routing: RoutingResult, query: str, db: AsyncSession, _ctx: dict
) -> dict[str, Any]:
    batches = await _all_latest_batches(db)
    if not batches:
        return _base_response(
            title="Top Priority Machines — No Data",
            intent=routing.intent, query=query, confidence=routing.confidence,
        )

    _pri_order = {"emergency": 0, "urgent": 1, "routine": 2}

    def sort_key(b: ReportBatchModel):
        pri = (b.priority or "").lower()
        rj = b.full_report_json or {}
        risk = rj.get("risk_score") or 0.0
        return (_pri_order.get(pri, 3), -float(risk))

    top3 = sorted(batches, key=sort_key)[:3]
    reports = [b.full_report_json or {} for b in top3]

    return _base_response(
        title=f"Top {len(top3)} Priority Machines",
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=reports,
    )


async def _execute_emergency_machines(
    routing: RoutingResult, query: str, db: AsyncSession, _ctx: dict
) -> dict[str, Any]:
    batches = await _all_latest_batches(db)
    emergency = [b for b in batches if (b.priority or "").lower() == "emergency"]

    if not emergency:
        return _base_response(
            title="Emergency Machines — None Found",
            intent=routing.intent, query=query, confidence=routing.confidence,
            copilot_answer="No machines are currently flagged as Emergency priority.",
        )

    machines = []
    for b in emergency:
        if b.machine:
            m_dict = _machine_to_dict(b.machine)
            rj = b.full_report_json or {}
            m_dict["rul_hours"] = b.rul_hours
            m_dict["risk_score"] = rj.get("risk_score")
            machines.append(m_dict)

    return _base_response(
        title=f"Emergency Machines ({len(emergency)})",
        intent=routing.intent, query=query, confidence=routing.confidence,
        machines=machines,
        copilot_answer=f"{len(emergency)} machine(s) require immediate attention.",
    )


async def _execute_low_confidence_machines(
    routing: RoutingResult, query: str, db: AsyncSession, _ctx: dict
) -> dict[str, Any]:
    batches = await _all_latest_batches(db)
    low = [
        b for b in batches
        if b.confidence is not None and b.confidence < 0.7
    ]

    if not low:
        return _base_response(
            title="Low Confidence Machines — None Found",
            intent=routing.intent, query=query, confidence=routing.confidence,
            copilot_answer="All analysed machines have diagnosis confidence above 0.7.",
        )

    machines = []
    for b in sorted(low, key=lambda b: b.confidence or 1.0):
        if b.machine:
            m_dict = _machine_to_dict(b.machine)
            m_dict["confidence"] = b.confidence
            m_dict["root_cause"] = b.root_cause
            machines.append(m_dict)

    return _base_response(
        title=f"Low Confidence Machines ({len(low)})",
        intent=routing.intent, query=query, confidence=routing.confidence,
        machines=machines,
        copilot_answer=f"{len(low)} machine(s) have diagnosis confidence below 0.7 and require manual inspection.",
    )


async def _execute_rul_query(
    routing: RoutingResult, query: str, db: AsyncSession, ctx: dict
) -> dict[str, Any]:
    # Session memory: if user says "its RUL" → resolve last machine
    if routing.has_reference and ctx.get("last_machine_id"):
        machine, report = await _resolve_machine_from_context(ctx["last_machine_id"], db)
        if machine and report:
            copilot_answer = await _get_copilot_answer(report, query)
            name = machine.machine_name
            rul = report.get("rul_hours")
            rul_str = f"{rul:.0f} hours" if rul is not None else "not available"
            return _base_response(
                title=f"RUL: {name}",
                intent=routing.intent, query=query, confidence=routing.confidence,
                reports=[report],
                copilot_answer=copilot_answer or f"RUL for {name} is {rul_str}.",
            )

    # Machine type specified → investigate that type
    if routing.target_machine:
        machines = await _machines_by_type(db, routing.target_machine)
        if machines:
            report = await _resolve_report(machines[0], db)
            copilot_answer = await _get_copilot_answer(report, query)
            return _base_response(
                title=f"RUL: {machines[0].machine_name}",
                intent=routing.intent, query=query, confidence=routing.confidence,
                reports=[report], copilot_answer=copilot_answer,
            )

    # Default: find machine(s) with lowest RUL (most urgent)
    batches = await _all_latest_batches(db)
    with_rul = [b for b in batches if b.rul_hours is not None]
    if not with_rul:
        return _base_response(
            title="RUL Query — No Data",
            intent=routing.intent, query=query, confidence=routing.confidence,
            copilot_answer="No RUL data available yet. Run ingestion to generate prognostic estimates.",
        )

    lowest = sorted(with_rul, key=lambda b: b.rul_hours or float("inf"))[:3]
    reports = [b.full_report_json or {} for b in lowest]

    return _base_response(
        title=f"Lowest RUL — {lowest[0].machine.machine_name if lowest[0].machine else 'Machine'}",
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=reports,
    )


async def _execute_investigate_machine(
    routing: RoutingResult, query: str, db: AsyncSession, ctx: dict
) -> dict[str, Any]:
    # Session memory: pronoun reference → use last machine
    if routing.has_reference and ctx.get("last_machine_id"):
        machine, report = await _resolve_machine_from_context(ctx["last_machine_id"], db)
        if machine and report:
            copilot_answer = await _get_copilot_answer(report, query)
            return _base_response(
                title=f"Analysis: {machine.machine_name}",
                intent=routing.intent, query=query, confidence=routing.confidence,
                reports=[report], copilot_answer=copilot_answer,
            )

    # Machine type keyword → find by type
    if routing.target_machine:
        machines = await _machines_by_type(db, routing.target_machine)
        type_label = routing.target_machine.title()
    else:
        machines = await _top_machines(db, limit=1)
        type_label = "Machine"

    if not machines:
        return _base_response(
            title=f"Analysis: No {type_label} Found",
            intent=routing.intent, query=query, confidence=routing.confidence,
        )

    reports = [await _resolve_report(m, db) for m in machines]
    copilot_answer = await _get_copilot_answer(reports[0], query) if len(reports) == 1 else None
    title = (
        f"Analysis: {machines[0].machine_name}"
        if len(machines) == 1
        else f"Analysis: {len(machines)} {type_label}s"
    )

    return _base_response(
        title=title,
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=reports, copilot_answer=copilot_answer,
    )


async def _execute_prioritize_today(
    routing: RoutingResult, query: str, db: AsyncSession, ctx: dict
) -> dict[str, Any]:
    machines = await _top_machines(db, limit=_MAX_MACHINES_PER_REQUEST)
    if not machines:
        return _base_response(
            title="Maintenance Priority — No Machines Found",
            intent=routing.intent, query=query, confidence=routing.confidence,
        )
    reports = [await _resolve_report(m, db) for m in machines]
    return _base_response(
        title=f"Maintenance Priority — Top {len(machines)} Machines",
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=reports,
    )


async def _execute_critical_machines(
    routing: RoutingResult, query: str, db: AsyncSession, ctx: dict
) -> dict[str, Any]:
    machines = await _critical_machines(db)
    return _base_response(
        title=f"Critical Machines ({len(machines)} found)",
        intent=routing.intent, query=query, confidence=routing.confidence,
        machines=[_machine_to_dict(m) for m in machines],
    )


async def _execute_daily_report(
    routing: RoutingResult, query: str, db: AsyncSession, ctx: dict
) -> dict[str, Any]:
    machines = await _top_machines(db, limit=_MAX_MACHINES_PER_REQUEST)
    if not machines:
        return _base_response(
            title="Daily Reliability Report — No Machines Found",
            intent=routing.intent, query=query, confidence=routing.confidence,
        )
    reports = [await _resolve_report(m, db) for m in machines]
    return _base_response(
        title=f"Daily Reliability Report — {len(machines)} Machines",
        intent=routing.intent, query=query, confidence=routing.confidence,
        reports=reports,
    )


# ── dispatcher ────────────────────────────────────────────────────────────────

_INTENT_HANDLERS: dict[str, Any] = {
    "plant_summary":            _execute_plant_summary,
    "highest_risk":             _execute_highest_risk,
    "top_priority":             _execute_top_priority,
    "emergency_machines":       _execute_emergency_machines,
    "low_confidence_machines":  _execute_low_confidence_machines,
    "rul_query":                _execute_rul_query,
    "investigate_machine":      _execute_investigate_machine,
    "prioritize_today":         _execute_prioritize_today,
    "critical_machines":        _execute_critical_machines,
    "daily_report":             _execute_daily_report,
}


async def execute_from_intent(
    routing: RoutingResult,
    query: str,
    db: AsyncSession,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Execute the copilot response for a classified intent.

    Read-only: reads report_batches and machines tables.
    Never calls run_pipeline(). Never re-runs agents.

    Args:
        routing:         Classified intent from query_router.route().
        query:           Original user query.
        db:              Async SQLAlchemy session.
        session_context: Optional dict with last_machine_id and last_intent
                         for session memory resolution.

    Returns:
        Structured response dict the frontend expects.

    Raises:
        ValueError: Unrecognised intent.
    """
    ctx = session_context or {}
    handler = _INTENT_HANDLERS.get(routing.intent)
    if handler is None:
        raise ValueError(f"Unrecognised intent: '{routing.intent}'")

    return await handler(routing, query, db, ctx)
