"""
Engineer Feedback API — submit and retrieve field corrections for diagnoses.

Endpoints:
  POST   /api/v1/reports/{batch_id}/feedback
  GET    /api/v1/reports/{batch_id}/feedback
  GET    /api/v1/feedback/recent?machine_id=...&limit=10
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.engineer_feedback import EngineerFeedback
from app.models.report_batch import ReportBatch
from app.schemas.engineer_feedback import FeedbackCreate, FeedbackSchema

router = APIRouter(tags=["feedback"])


async def _get_batch_or_404(batch_id: uuid.UUID, db: AsyncSession) -> ReportBatch:
    result = await db.execute(
        select(ReportBatch).where(ReportBatch.batch_id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report batch {batch_id} not found",
        )
    return batch


def _extract_diagnosis(batch: ReportBatch) -> tuple[str | None, str | None]:
    """Pull failure_mode and root_cause from the stored full_report_json."""
    report = batch.full_report_json or {}
    if isinstance(report, str):
        import json
        try:
            report = json.loads(report)
        except Exception:
            report = {}
    return report.get("failure_mode"), report.get("root_cause")


@router.post("/reports/{batch_id}/feedback", response_model=FeedbackSchema)
async def submit_feedback(
    batch_id: uuid.UUID,
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
) -> FeedbackSchema:
    """Create or update feedback for a report batch (upsert by batch_id + engineer_id)."""
    batch = await _get_batch_or_404(batch_id, db)
    failure_mode, reported_root_cause = _extract_diagnosis(batch)

    engineer_id = body.engineer_id  # may be None — treated as anonymous

    # Attempt upsert: look for existing row
    existing_result = await db.execute(
        select(EngineerFeedback).where(
            EngineerFeedback.report_batch_id == batch_id,
            EngineerFeedback.engineer_id == engineer_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # Update in place
        if body.thumbs is not None:
            existing.thumbs = body.thumbs
        if body.verdict is not None:
            existing.verdict = body.verdict
        if body.actual_root_cause is not None:
            existing.actual_root_cause = body.actual_root_cause
        if body.notes is not None:
            existing.notes = body.notes
        existing.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(existing)

        if body.verdict == "wrong":
            try:
                from app.services.alert_bus import get_alert_bus, make_contested_diagnosis_alert
                alert = make_contested_diagnosis_alert(
                    machine_id=str(batch.machine_id),
                    machine_name=None,
                    reported_root_cause=reported_root_cause,
                    actual_root_cause=body.actual_root_cause,
                    feedback_id=str(existing.feedback_id),
                )
                get_alert_bus().publish(alert)
            except Exception as _exc:
                import logging
                logging.getLogger(__name__).warning(
                    "alert_bus publish (contested update) failed: %s", _exc
                )

        return FeedbackSchema.model_validate(existing)

    # Insert new row
    row = EngineerFeedback(
        report_batch_id=batch_id,
        machine_id=batch.machine_id,
        failure_mode=failure_mode,
        reported_root_cause=reported_root_cause,
        thumbs=body.thumbs,
        verdict=body.verdict,
        actual_root_cause=body.actual_root_cause,
        notes=body.notes,
        engineer_id=engineer_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Publish contested-diagnosis alert when verdict is "wrong"
    if body.verdict == "wrong":
        try:
            from app.services.alert_bus import get_alert_bus, make_contested_diagnosis_alert
            alert = make_contested_diagnosis_alert(
                machine_id=str(batch.machine_id),
                machine_name=None,  # not stored on ReportBatch; UI resolves via machine_id
                reported_root_cause=reported_root_cause,
                actual_root_cause=body.actual_root_cause,
                feedback_id=str(row.feedback_id),
            )
            get_alert_bus().publish(alert)
        except Exception as _exc:
            import logging
            logging.getLogger(__name__).warning(
                "alert_bus publish (contested) failed: %s", _exc
            )

    return FeedbackSchema.model_validate(row)


@router.get("/reports/{batch_id}/feedback", response_model=list[FeedbackSchema])
async def get_batch_feedback(
    batch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackSchema]:
    """List all feedback rows for a report batch."""
    await _get_batch_or_404(batch_id, db)
    result = await db.execute(
        select(EngineerFeedback)
        .where(EngineerFeedback.report_batch_id == batch_id)
        .order_by(EngineerFeedback.created_at.desc())
    )
    rows = result.scalars().all()
    return [FeedbackSchema.model_validate(r) for r in rows]


@router.get("/feedback/recent")
async def get_recent_feedback(
    machine_id: uuid.UUID = Query(..., description="Filter by machine UUID"),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackSchema]:
    """Return recent feedback rows for a machine, newest first."""
    result = await db.execute(
        select(EngineerFeedback)
        .where(EngineerFeedback.machine_id == machine_id)
        .order_by(EngineerFeedback.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [FeedbackSchema.model_validate(r) for r in rows]
