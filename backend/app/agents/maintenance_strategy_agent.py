"""
Maintenance Strategy Agent — ReAct LLM agent that proposes a constraint-aware
repair plan by querying spare-parts inventory, crew availability, and cost
estimates before committing to a plan.

Tools:
  check_spares_inventory    — DB lookup in spare_parts table
  get_crew_availability     — deterministic stub (TODO: real scheduler)
  estimate_repair_cost      — sums unit_cost_usd + labor at $80/hr
  check_dependent_machines  — correlated failure lookup (stub)
  propose_plan              — structured final output

Input  : state (VulcanOpsState) — needs state.diagnosis, state.impact
Output : AgentResult with data matching StrategyDecision + new fields
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, text

from app.agents.base import AgentResult
from app.core.enums import MaintenancePriority, RiskLevel
from app.core.state_contract import VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.spare_part import SparePart
from app.services.llm_service import LLMError, llm_service
from sqlalchemy import select

_MAX_ITERATIONS = 4

# ── priority matrix (kept deterministic — used as fallback for system prompt) ──

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

_LABOR_RATE_USD_PER_HOUR = 80.0

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_spares_inventory",
            "description": (
                "Search the spare_parts inventory table for parts matching a keyword. "
                "Returns qty_on_hand, lead_time_days, unit_cost_usd for each match. "
                "Use this to determine if required parts are in stock."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part_keyword": {
                        "type": "string",
                        "description": "Keyword to match against part_name or category (e.g. 'bearing', 'seal')",
                    }
                },
                "required": ["part_keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crew_availability",
            "description": (
                "Check whether a maintenance crew is available to start work. "
                "Returns available=true/false and any constraints."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "ISO date string for intended start (e.g. 'today', '2026-06-20')",
                    },
                    "hours_needed": {
                        "type": "integer",
                        "description": "Estimated repair duration in hours",
                        "minimum": 1,
                    },
                },
                "required": ["start_date", "hours_needed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_repair_cost",
            "description": "Estimate total repair cost: parts from inventory + labor at $80/hr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "parts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of part name keywords to price from inventory",
                    },
                    "labor_hours": {
                        "type": "integer",
                        "description": "Expected labor hours",
                        "minimum": 1,
                    },
                },
                "required": ["parts", "labor_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_dependent_machines",
            "description": (
                "Identify other machines whose failures may be correlated with this one "
                "based on shared failure modes in maintenance history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "machine_id": {
                        "type": "string",
                        "description": "UUID of the machine being analysed",
                    }
                },
                "required": ["machine_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "Submit the final maintenance plan. Call this when done researching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "immediate_action": {
                        "type": "string",
                        "description": "Action to take immediately (shutdown, reduce load, etc.)",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["emergency", "urgent", "scheduled", "routine"],
                    },
                    "estimated_repair_hours": {
                        "type": "number",
                        "minimum": 0,
                    },
                    "parts_required": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific parts needed (use names from inventory if possible)",
                    },
                    "safety_notes": {"type": "string"},
                    "resource_requirements": {"type": "string"},
                    "procurement_strategy": {
                        "type": "string",
                        "description": (
                            "How to handle parts that are out of stock or have lead time > RUL. "
                            "E.g. 'Expedite bearing order from SKF; arrange emergency delivery.'"
                        ),
                    },
                    "constraint_violations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of constraint issues found (e.g. 'bearing out of stock, 21d lead')",
                    },
                },
                "required": [
                    "immediate_action", "priority", "estimated_repair_hours",
                    "parts_required", "safety_notes", "resource_requirements",
                    "procurement_strategy", "constraint_violations",
                ],
            },
        },
    },
]

_SYSTEM_PROMPT = (
    "You are a maintenance planning agent for an industrial facility. "
    "Your task is to propose a constraint-aware repair plan based on diagnosis and inventory.\n\n"
    "RULES:\n"
    "- Call check_spares_inventory for the key parts the diagnosis implies (bearing, seal, coupling, etc.).\n"
    "- If parts are OUT OF STOCK (qty_on_hand=0) or lead_time_days exceeds the machine's RUL, "
    "your plan MUST include a procurement_strategy and flag the issue in constraint_violations.\n"
    "- Call estimate_repair_cost once to get a cost estimate.\n"
    "- Call propose_plan last with a complete, actionable plan.\n"
    "- Be specific: reference actual part names from inventory where possible.\n"
    "- Call only one tool per turn."
)


def _build_initial_context(state: VulcanOpsState) -> str:
    diag = state.diagnosis
    impact = state.impact
    machine = state.machine_context
    rul = state.rul_prediction

    risk_level = impact.risk_level.value if impact and impact.risk_level else "unknown"
    rul_hours = rul.remaining_useful_life_hours if rul else None
    rul_str = f"{rul_hours:.0f}h" if rul_hours is not None else "unknown"
    machine_desc = f"{machine.machine_name} ({machine.machine_type})" if machine else "Unknown machine"

    # Derive expected priority from matrix for context
    rul_bucket = _rul_bucket(rul_hours)
    priority = _PRIORITY_MATRIX.get(
        (risk_level, rul_bucket), MaintenancePriority.SCHEDULED
    ).value

    parts = [
        f"Machine: {machine_desc}",
        f"Risk level: {risk_level}",
        f"Estimated RUL: {rul_str}",
        f"Suggested priority: {priority}",
        "",
        "Diagnosis to plan around:",
        f"  failure_mode: {diag.failure_mode if diag else 'unknown'}",
        f"  root_cause:   {diag.root_cause if diag else 'unknown'}",
        f"  confidence:   {diag.confidence if diag else 'N/A'}",
        "",
    ]

    if impact:
        parts += [
            "Impact assessment:",
            f"  estimated_downtime_hours: {impact.estimated_downtime_hours}",
            f"  estimated_cost_usd:       {impact.estimated_cost_usd}",
            "",
        ]

    parts.append(
        "Search inventory for required parts, estimate repair cost, then call propose_plan."
    )
    return "\n".join(parts)


# ── tool implementations ──────────────────────────────────────────────────────

async def _check_spares_inventory(part_keyword: str) -> str:
    kw = f"%{part_keyword.strip()}%"
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SparePart)
                .where(
                    or_(
                        SparePart.part_name.ilike(kw),
                        SparePart.category.ilike(kw),
                    )
                )
                .order_by(SparePart.qty_on_hand.desc())
                .limit(8)
            )
            rows = result.scalars().all()
    except Exception as exc:
        return f"Inventory query failed: {exc}"

    if not rows:
        return f"No parts matching '{part_keyword}' found in inventory."

    lines = []
    for r in rows:
        stock_flag = " ⚠ OUT OF STOCK" if r.qty_on_hand == 0 else ""
        reorder_flag = (
            f" (below reorder threshold of {r.reorder_threshold})"
            if r.qty_on_hand <= r.reorder_threshold and r.qty_on_hand > 0
            else ""
        )
        cost = f"${r.unit_cost_usd}" if r.unit_cost_usd else "price unknown"
        lines.append(
            f"  {r.part_name} | cat={r.category} | qty={r.qty_on_hand}{stock_flag}{reorder_flag} "
            f"| lead={r.lead_time_days}d | {cost} | supplier={r.supplier or 'unknown'}"
        )
    return "\n".join(lines)


def _get_crew_availability(start_date: str, hours_needed: int) -> str:
    # TODO: integrate with real crew scheduling system
    if hours_needed <= 8:
        return (
            f"Crew available for {start_date}. "
            f"{hours_needed}h is within a single shift. No scheduling conflict."
        )
    return (
        f"WARNING: {hours_needed}h repair exceeds a single 8h shift. "
        f"Multi-shift crew required for {start_date}. Coordinate with shift supervisor."
    )


async def _estimate_repair_cost(parts: list[str], labor_hours: int) -> str:
    labor_cost = labor_hours * _LABOR_RATE_USD_PER_HOUR
    parts_cost = 0.0
    not_found: list[str] = []

    try:
        async with AsyncSessionLocal() as db:
            for keyword in parts:
                kw = f"%{keyword.strip()}%"
                result = await db.execute(
                    select(SparePart)
                    .where(
                        or_(
                            SparePart.part_name.ilike(kw),
                            SparePart.category.ilike(kw),
                        )
                    )
                    .order_by(SparePart.unit_cost_usd.asc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                if row and row.unit_cost_usd:
                    parts_cost += float(row.unit_cost_usd)
                else:
                    not_found.append(keyword)
    except Exception as exc:
        return f"Cost estimation failed: {exc}"

    total = parts_cost + labor_cost
    result_lines = [
        f"Parts cost:  ${parts_cost:.2f}",
        f"Labor cost:  ${labor_cost:.2f}  ({labor_hours}h × ${_LABOR_RATE_USD_PER_HOUR:.0f}/hr)",
        f"Total:       ${total:.2f}",
    ]
    if not_found:
        result_lines.append(f"Note: no price found for: {', '.join(not_found)}")
    return "\n".join(result_lines)


async def _check_dependent_machines(machine_id: str) -> str:
    # TODO: implement correlation via shared failure_mode in maintenance_records across machines
    # For now return empty result rather than blocking the plan
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("""
                    SELECT DISTINCT m.machine_name
                    FROM maintenance_records mr
                    JOIN machines m ON m.machine_id = mr.machine_id
                    WHERE mr.machine_id != :mid::uuid
                      AND mr.failure_mode IN (
                          SELECT failure_mode FROM maintenance_records
                          WHERE machine_id = :mid::uuid
                            AND failure_mode IS NOT NULL
                      )
                    LIMIT 5
                """),
                {"mid": machine_id},
            )
            rows = result.fetchall()
    except Exception:
        return "Dependency check unavailable (query error)."

    if not rows:
        return "No dependent machines found with correlated failure modes."
    names = [r[0] for r in rows]
    return f"Correlated failures found on: {', '.join(names)}. Consider fleet-wide inspection."


async def _execute_tool(action: str, action_input: dict[str, Any], state: VulcanOpsState) -> str:
    if action == "check_spares_inventory":
        return await _check_spares_inventory(action_input.get("part_keyword", ""))
    if action == "get_crew_availability":
        return _get_crew_availability(
            action_input.get("start_date", "today"),
            int(action_input.get("hours_needed", 4)),
        )
    if action == "estimate_repair_cost":
        return await _estimate_repair_cost(
            action_input.get("parts", []),
            int(action_input.get("labor_hours", 4)),
        )
    if action == "check_dependent_machines":
        return await _check_dependent_machines(
            action_input.get("machine_id", str(state.active_machine_id))
        )
    return f"Unknown tool: {action}"


# ── deterministic fallback (keep until LLM produces equivalent quality) ────────

def _deterministic_fallback(state: VulcanOpsState) -> dict[str, Any]:
    """Preserved from old agent — used when LLM is unavailable."""
    from app.core.enums import RiskLevel

    machine_type = (state.machine_context.machine_type if state.machine_context else "").lower()
    failure_mode = (state.diagnosis.failure_mode if state.diagnosis else "").lower()
    root_cause = (state.diagnosis.root_cause if state.diagnosis else "").lower()
    sensor = (state.anomaly.sensor if state.anomaly else "")
    combined = f"{failure_mode} {root_cause}"

    parts: list[str] = []
    if "bearing" in combined or sensor == "vibration":
        parts += ["Replacement bearings (OEM spec)", "Bearing grease / lubricant"]
    if "seal" in combined or sensor == "pressure":
        parts += ["Seal kit", "O-rings"]
    if "thermal" in combined or sensor == "temperature":
        parts += ["Thermal gaskets", "Cooling fluid"]
    if "coupling" in combined:
        parts += ["Coupling alignment kit", "Replacement coupling inserts"]
    if "lubrication" in combined or "oil" in combined:
        parts += ["Correct grade lubricant / oil", "Oil filter"]
    if "pump" in machine_type:
        parts += ["Impeller inspection kit", "Mechanical seal"]
    if not parts:
        parts = ["Parts to be determined by maintenance team following inspection"]

    risk_level = (
        state.impact.risk_level.value if state.impact and state.impact.risk_level else "medium"
    )
    rul_hours = (
        state.rul_prediction.remaining_useful_life_hours if state.rul_prediction else None
    )
    bucket = _rul_bucket(rul_hours)
    priority = _PRIORITY_MATRIX.get((risk_level, bucket), MaintenancePriority.SCHEDULED)
    repair_hours = {
        MaintenancePriority.EMERGENCY: 12.0,
        MaintenancePriority.URGENT: 6.0,
        MaintenancePriority.SCHEDULED: 4.0,
        MaintenancePriority.ROUTINE: 2.0,
    }[priority]

    machine_name = state.machine_context.machine_name if state.machine_context else "the machine"
    return {
        "immediate_action": f"Schedule {priority.value}-priority maintenance for {machine_name}.",
        "priority": priority.value,
        "estimated_repair_hours": repair_hours,
        "parts_required": parts,
        "safety_notes": "LOTO required. PPE mandatory. Follow site safety protocol.",
        "resource_requirements": (
            f"2 technicians minimum. Estimated {repair_hours}h. "
            "Verify spare parts availability before scheduling."
        ),
        "procurement_strategy": "Check inventory before scheduling — verify parts availability.",
        "constraint_violations": [],
    }


# ── main entry point ──────────────────────────────────────────────────────────

async def run(state: VulcanOpsState) -> AgentResult:
    if not state.impact:
        return AgentResult(
            status="error",
            data={},
            errors=["impact assessment is required before generating maintenance strategy"],
        )

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_initial_context(state)},
    ]
    used_tool_keys: set[str] = set()
    plan: dict[str, Any] | None = None

    for iteration in range(1, _MAX_ITERATIONS + 1):
        try:
            result = await llm_service.call_with_tools(
                agent="maintenance_strategy_agent",
                system=_SYSTEM_PROMPT,
                messages=messages,
                tools=_TOOLS,
            )
        except LLMError as exc:
            print(
                f"[maintenance_strategy_agent] LLM unavailable ({type(exc).__name__}); "
                "using deterministic fallback",
                flush=True,
            )
            plan = _deterministic_fallback(state)
            break

        thought = result.content or "(no narration)"
        action = result.tool_name or ""
        action_input = result.tool_args or {}
        tool_call_id = result.tool_call_id or f"synthetic-{iteration}"

        if result.kind == "final" or not action:
            print(
                f"[maintenance_strategy_agent] iteration={iteration} final-text fallback; "
                "using deterministic plan",
                flush=True,
            )
            plan = _deterministic_fallback(state)
            break

        if action == "propose_plan":
            plan = action_input
            print(
                f"[maintenance_strategy_agent] iteration={iteration} plan proposed: "
                f"priority={action_input.get('priority')} "
                f"violations={len(action_input.get('constraint_violations', []))}",
                flush=True,
            )
            break

        tool_key = f"{action}:{json.dumps(action_input, sort_keys=True)}"
        if tool_key in used_tool_keys:
            observation = (
                f"You already called '{action}' with the same arguments. "
                "Try a different tool or call propose_plan."
            )
        else:
            used_tool_keys.add(tool_key)
            try:
                observation = await _execute_tool(action, action_input, state)
            except Exception as exc:
                observation = f"Tool {action} failed: {exc}"

        print(
            f"[maintenance_strategy_agent] iteration={iteration} action={action} "
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

    if plan is None:
        print(
            "[maintenance_strategy_agent] loop exhausted without propose_plan; "
            "using deterministic fallback",
            flush=True,
        )
        plan = _deterministic_fallback(state)

    # Map priority string → enum, tolerating case differences
    raw_priority = plan.get("priority", "scheduled").lower()
    try:
        priority_enum = MaintenancePriority(raw_priority)
    except ValueError:
        priority_enum = MaintenancePriority.SCHEDULED

    return AgentResult(
        status="success",
        data={
            "immediate_action": plan.get("immediate_action", ""),
            "priority": priority_enum.value,
            "estimated_repair_hours": float(plan.get("estimated_repair_hours", 4.0)),
            "parts_required": plan.get("parts_required", []),
            "safety_notes": plan.get("safety_notes", ""),
            "resource_requirements": plan.get("resource_requirements", ""),
            "procurement_strategy": plan.get("procurement_strategy", ""),
            "constraint_violations": plan.get("constraint_violations", []),
        },
    )
