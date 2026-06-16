"""
Report Persistence Service — stores pipeline results for later retrieval.

Two persistence paths:
  persist_batch()      — full deep-analysis result (all 9 agents ran, LLM included).
  persist_fast_batch() — fast-only result (5 non-LLM agents, top-N machines queued
                         for deep analysis at a later pass or on demand).

The deep_analysis_status key in full_report_json distinguishes the two:
  "done"   → deep analysis completed.
  "queued" → only fast agents ran; machine was below the risk threshold for this run.

This module is called by the ingest orchestrator after a pipeline run finishes.
It writes one `ReportBatch` row plus three `StoredRoleReport` rows per machine.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_contract import VulcanOpsState
from app.models.ingestion_event import IngestionEvent
from app.models.report_batch import ReportBatch
from app.models.stored_role_report import StoredRoleReport


async def persist_batch(
    event_id: uuid.UUID,
    state: VulcanOpsState,
    report: dict,
    db: AsyncSession,
) -> ReportBatch:
    """
    Save one pipeline result to `report_batches` + `stored_role_reports`.

    Args:
        event_id: The parent ingestion event UUID.
        state: Final VulcanOpsState returned by the pipeline.
        report: Frontend-facing report dict from `build_single_report(state)`.
        db: Active async SQLAlchemy session.

    Returns:
        The persisted ReportBatch instance (attached to db session).
    """
    report.setdefault("deep_analysis_status", "done")

    batch = ReportBatch(
        event_id=event_id,
        machine_id=state.active_machine_id,
        root_cause=report.get("root_cause"),
        failure_mode=report.get("failure_mode"),
        confidence=report.get("diagnosis_confidence"),
        risk_level=report.get("risk_level"),
        recommended_action=report.get("recommended_action"),
        priority=report.get("priority"),
        rul_hours=report.get("rul_hours"),
        verification_passed=report.get("verification", {}).get("verified"),
        pipeline_errors=report.get("pipeline_errors", 0),
        full_report_json=report,
    )
    db.add(batch)
    await db.flush()  # obtain batch_id

    for role in ("engineer", "supervisor", "manager"):
        content = report.get(f"{role}_report") or ""
        db.add(
            StoredRoleReport(
                batch_id=batch.batch_id,
                role=role,
                content=content,
            )
        )

    await db.commit()
    return batch


async def persist_fast_batch(
    event_id: uuid.UUID,
    state: VulcanOpsState,
    risk_score: float,
    db: AsyncSession,
) -> ReportBatch:
    """
    Persist fast-analysis results for a machine that did not receive deep analysis.

    Only anomaly, prognostics, evidence_retrieval, operational_impact, and
    plant_priority outputs are available. Diagnosis, verification, strategy,
    and role reports are None.

    The stored report has deep_analysis_status="queued" to distinguish it from
    a fully-processed batch.
    """
    from app.services import report_builder  # local import avoids circular

    report = report_builder.build_single_report(state)
    report["deep_analysis_status"] = "queued"
    report["risk_score"] = round(risk_score, 1)

    batch = ReportBatch(
        event_id=event_id,
        machine_id=state.active_machine_id,
        root_cause=None,
        failure_mode=None,
        confidence=None,
        risk_level=report.get("risk_level"),
        recommended_action=None,
        priority=state.priority.value if state.priority else None,
        rul_hours=report.get("rul_hours"),
        verification_passed=None,
        pipeline_errors=len(state.errors),
        full_report_json=report,
    )
    db.add(batch)
    await db.commit()
    return batch


async def update_event_status(
    event_id: uuid.UUID,
    status: str,
    db: AsyncSession,
    completed_at: datetime | None = None,
    machines_found: int | None = None,
) -> None:
    """
    Update an ingestion event's status and optional metadata.

    Args:
        event_id: The ingestion event UUID.
        status: One of 'pending' | 'running' | 'processing' | 'done' | 'failed'.
        db: Active async SQLAlchemy session.
        completed_at: Optional timestamp to set when the event finishes.
        machines_found: Optional count of machines discovered in the event.
    """
    result = await db.execute(
        select(IngestionEvent)
        .where(IngestionEvent.event_id == event_id)
        .with_for_update()
    )
    event = result.scalar_one_or_none()
    if event is None:
        return

    event.status = status
    if completed_at is not None:
        event.completed_at = completed_at
    if machines_found is not None:
        event.machines_found = machines_found

    await db.commit()


async def mark_event_done(
    event_id: uuid.UUID,
    db: AsyncSession,
    machines_found: int | None = None,
) -> None:
    """Convenience helper: mark an ingestion event as completed now."""
    await update_event_status(
        event_id=event_id,
        status="done",
        db=db,
        completed_at=datetime.now(timezone.utc),
        machines_found=machines_found,
    )


async def mark_event_failed(
    event_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Convenience helper: mark an ingestion event as failed now."""
    await update_event_status(
        event_id=event_id,
        status="failed",
        db=db,
        completed_at=datetime.now(timezone.utc),
    )
