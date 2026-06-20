"""
Supervisor Agent — cheap, single-shot LLM planner with a deterministic baseline.

Inspects the initial machine state and decides which downstream agents to run.
This avoids burning budget on agents that cannot contribute (e.g. evidence
retrieval when no documents are ingested, or plant-level prioritization when
maintenance history is too thin to compute a meaningful fleet rank).

A data-driven heuristic produces a baseline plan first; the LLM is then asked
to refine it. If the LLM is unavailable or returns garbage, the heuristic plan
is used, so the pipeline still adapts to the available data.

Input  : state after data loading (machine_context, sensor_readings,
         maintenance_history, retrieved_evidence)
Output : AgentResult.data = {
    "execution_plan": {
        "stages":   [...],
        "skipped":  {...},
        "rationale": "..."
    }
}
"""

from sqlalchemy import func, select

from app.agents.base import AgentResult
from app.core.state_contract import ExecutionPlan, VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.document_chunk import DocumentChunk
from app.services.llm_service import LLMError, llm_service
from pydantic import ValidationError

_AVAILABLE_STAGES = [
    "anomaly",
    "prognostics",
    "evidence_retrieval",
    "diagnosis",
    "evidence_verification",
    "operational_impact",
    "maintenance_strategy",
    "plant_priority",
    "communication",
]

# Thresholds for the deterministic heuristic baseline plan.
_MIN_SENSOR_READINGS_FOR_PROGNOSTICS = 20
_MIN_HISTORY_FOR_PROGNOSTICS = 5
_MIN_HISTORY_FOR_PLANT_PRIORITY = 30


def _full_plan() -> ExecutionPlan:
    """Conservative fallback when every stage is warranted."""
    return ExecutionPlan(
        stages=list(_AVAILABLE_STAGES),
        skipped={},
        rationale="Run the full 9-stage pipeline.",
    )


_SUPERVISOR_SYSTEM_PROMPT = (
    "You are a fast pipeline supervisor. Your job is to pick which agents to run.\n"
    "Stages (in order): anomaly, prognostics, evidence_retrieval, diagnosis, "
    "evidence_verification, operational_impact, maintenance_strategy, plant_priority, communication.\n"
    "\n"
    "Hard rules (never violate):\n"
    "- anomaly MUST run if sensor_readings exist.\n"
    "- diagnosis requires anomaly.\n"
    "- evidence_verification requires diagnosis.\n"
    "- communication ALWAYS runs.\n"
    "\n"
    "Skip guidance:\n"
    "- Skip evidence_retrieval if document_chunks=0.\n"
    "- Skip prognostics if there are very few sensor readings or no maintenance history to learn from.\n"
    "- Skip plant_priority if maintenance history is sparse (<30 records) — there is not enough fleet signal.\n"
    "- Skip evidence_verification if there is no diagnosis or no evidence to verify against.\n"
    "\n"
    "A heuristic baseline plan is provided. You may only REMOVE stages from it "
    "(e.g. skip plant_priority for thin history). You must NOT add stages back.\n"
    "Return ONLY JSON with this exact shape:\n"
    '{"stages":["..."], "skipped":{"stage":"reason"}, "rationale":"..."}'
)


async def _count_document_chunks() -> int:
    """Check whether any documents have been ingested into the semantic index."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count(DocumentChunk.chunk_id)))
        return result.scalar_one()


def _build_state_summary(state: VulcanOpsState, document_chunks: int) -> str:
    parts: list[str] = []

    if state.machine_context:
        m = state.machine_context
        parts.append(
            f"Machine: {m.machine_name} ({m.machine_type}), criticality={m.criticality.value}"
        )

    parts.append(f"document_chunks={document_chunks}")
    parts.append(f"sensor_readings={len(state.sensor_readings)}")
    if state.sensor_readings:
        latest = max(state.sensor_readings, key=lambda r: r.timestamp)
        sensor_parts = [
            f"{f}={getattr(latest, f)}"
            for f in ("temperature", "vibration", "pressure", "load", "rpm")
            if getattr(latest, f, None) is not None
        ]
        parts.append(f"latest={'; '.join(sensor_parts)}")

    history_count = len(state.maintenance_history)
    parts.append(f"maintenance_history={history_count}")
    if state.maintenance_history:
        rec = state.maintenance_history[0]
        parts.append(
            f"latest_history={rec.date.isoformat() if rec.date else 'unknown'}: "
            f"{rec.failure_mode}"
        )

    return "\n".join(parts)


async def _build_state_summary_async(state: VulcanOpsState) -> str:
    doc_chunks = await _count_document_chunks()
    return _build_state_summary(state, doc_chunks)


def _build_heuristic_plan(state: VulcanOpsState, document_chunks: int) -> ExecutionPlan:
    """
    Data-driven baseline plan.

    The heuristic is conservative: it only skips stages when the input data is
    clearly insufficient, so a skipped stage is a deliberate saving, not a risk.
    """
    sensor_count = len(state.sensor_readings)
    history_count = len(state.maintenance_history)
    has_history = history_count > 0
    has_documents = document_chunks > 0

    stages: list[str] = []
    skipped: dict[str, str] = {}

    # 1. Anomaly: always run when we have sensor readings.
    if sensor_count:
        stages.append("anomaly")
    else:
        skipped["anomaly"] = "No sensor readings available."

    # 2. Prognostics: needs enough readings + some history to learn degradation from.
    if (
        sensor_count >= _MIN_SENSOR_READINGS_FOR_PROGNOSTICS
        and history_count >= _MIN_HISTORY_FOR_PROGNOSTICS
    ):
        stages.append("prognostics")
    else:
        skipped["prognostics"] = (
            f"Insufficient data (sensor_readings={sensor_count}, history={history_count})."
        )

    # 3. Evidence retrieval: only useful if documents exist.
    if has_documents:
        stages.append("evidence_retrieval")
    else:
        skipped["evidence_retrieval"] = "No ingested documents to retrieve from."

    # 4. Diagnosis: run if anomaly stage is planned.
    if "anomaly" in stages:
        stages.append("diagnosis")
    else:
        skipped["diagnosis"] = "Cannot diagnose without anomaly detection."

    # 5. Evidence verification: needs diagnosis + something to verify against.
    if "diagnosis" in stages and (has_documents or has_history):
        stages.append("evidence_verification")
    else:
        skipped["evidence_verification"] = (
            "Skipped: requires diagnosis and at least one evidence source."
        )

    # 6. Operational impact: meaningful only when we have a diagnosis.
    if "diagnosis" in stages:
        stages.append("operational_impact")
    else:
        skipped["operational_impact"] = "No diagnosis to assess impact from."

    # 7. Maintenance strategy: needs history or documents to ground recommendations.
    if has_history or has_documents:
        stages.append("maintenance_strategy")
    else:
        skipped["maintenance_strategy"] = "No history or documents to base a strategy on."

    # 8. Plant priority: needs rich history to rank against the fleet.
    if history_count >= _MIN_HISTORY_FOR_PLANT_PRIORITY:
        stages.append("plant_priority")
    else:
        skipped["plant_priority"] = (
            f"Maintenance history too sparse ({history_count} < {_MIN_HISTORY_FOR_PLANT_PRIORITY}) for fleet ranking."
        )

    # 9. Communication: always runs to produce role reports.
    stages.append("communication")

    return ExecutionPlan(
        stages=stages,
        skipped=skipped,
        rationale=(
            f"Heuristic baseline: sensor={sensor_count}, history={history_count}, "
            f"documents={document_chunks}."
        ),
    )


def _enforce_hard_rules(plan: ExecutionPlan, state: VulcanOpsState) -> ExecutionPlan:
    """Defensive re-check of the hard rules; mutates and returns the plan."""
    stages_set = set(plan.stages)
    skipped = dict(plan.skipped)

    # Rule 1: anomaly must run if sensor readings exist
    if state.sensor_readings and "anomaly" not in stages_set:
        plan.stages.insert(0, "anomaly")
        stages_set.add("anomaly")
        skipped.pop("anomaly", None)

    # Rule 2: diagnosis requires anomaly
    if "diagnosis" in stages_set and "anomaly" not in stages_set:
        stages_set.discard("diagnosis")
        plan.stages = [s for s in plan.stages if s != "diagnosis"]
        skipped["diagnosis"] = "Hard rule: diagnosis requires anomaly stage."

    # Rule 3: evidence_verification requires diagnosis
    if "evidence_verification" in stages_set and "diagnosis" not in stages_set:
        stages_set.discard("evidence_verification")
        plan.stages = [s for s in plan.stages if s != "evidence_verification"]
        skipped["evidence_verification"] = "Hard rule: verification requires diagnosis stage."

    # Rule 4: communication always runs
    if "communication" not in stages_set:
        plan.stages.append("communication")
        skipped.pop("communication", None)

    # Rule 5: evidence_verification must run when diagnosis is planned AND
    # maintenance history exists (enough evidence to challenge the diagnosis).
    # The LLM tends to drop this stage alongside evidence_retrieval; protect it
    # because it is now the adversarial ReAct agent and central to agentic value.
    if "diagnosis" in stages_set and state.maintenance_history and "evidence_verification" not in stages_set:
        try:
            diag_idx = plan.stages.index("diagnosis")
            plan.stages.insert(diag_idx + 1, "evidence_verification")
        except ValueError:
            plan.stages.append("evidence_verification")
        stages_set.add("evidence_verification")
        skipped.pop("evidence_verification", None)

    plan.skipped = skipped
    return plan


async def run(state: VulcanOpsState) -> AgentResult:
    document_chunks = await _count_document_chunks()
    heuristic_plan = _build_heuristic_plan(state, document_chunks)
    summary = _build_state_summary(state, document_chunks)

    user_prompt = (
        "Plan the pipeline for this machine. Return JSON only.\n\n"
        f"{summary}\n\n"
        "HEURISTIC BASELINE PLAN (you may only REMOVE stages from it):\n"
        f"{heuristic_plan.model_dump_json()}"
    )

    try:
        plan = await llm_service.call_structured(
            agent="supervisor_agent",
            system=_SUPERVISOR_SYSTEM_PROMPT,
            user=user_prompt,
            schema=ExecutionPlan,
        )
        # Sanitize: drop any stage names the LLM invented outside _AVAILABLE_STAGES.
        plan.stages = [s for s in plan.stages if s in _AVAILABLE_STAGES]
        if not plan.stages:
            plan = heuristic_plan
    except (LLMError, ValidationError) as exc:
        # Heuristic fallback: safe, deterministic, and already respects hard rules.
        print(f"[supervisor_agent] LLM plan failed ({type(exc).__name__}); using heuristic plan.", flush=True)
        plan = heuristic_plan

    plan = _enforce_hard_rules(plan, state)

    print(
        f"[supervisor_agent] plan={plan.stages} skipped={list(plan.skipped.keys())} "
        f"rationale={plan.rationale[:120]!r}",
        flush=True,
    )

    return AgentResult(
        status="success",
        data={
            "execution_plan": plan.model_dump(),
            "llm_telemetry": {},
        },
    )
