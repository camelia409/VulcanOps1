"""
Reports Route — query and download persisted pipeline reports.

All endpoints operate on the tables created by the ingestion event flow:
`ingestion_events`, `report_batches`, and `stored_role_reports`.
"""

import json
import logging
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.ingestion_event import IngestionEvent
from app.models.machine import Machine
from app.models.report_batch import ReportBatch
from app.models.stored_role_report import StoredRoleReport
from app.services.deep_analysis_execution_service import execute_deep_analysis_job
from app.services import deep_analysis_job_service
from app.services.pdf_service import generate_pdf_from_batch

router = APIRouter(prefix="/reports", tags=["reports"])

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


@router.get("")
async def list_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List ingestion events with a summary of their batches."""
    total_result = await db.execute(select(func.count(IngestionEvent.event_id)))
    total = total_result.scalar_one()

    result = await db.execute(
        select(IngestionEvent)
        .order_by(IngestionEvent.triggered_at.desc())
        .offset(skip)
        .limit(limit)
        .options(selectinload(IngestionEvent.batches).selectinload(ReportBatch.machine))
    )
    events = result.scalars().all()

    items = []
    for event in events:
        batch_summaries = [
            {
                "batch_id": str(b.batch_id),
                "machine_id": str(b.machine_id),
                "machine_name": b.machine.machine_name if b.machine else None,
                "generated_at": b.generated_at.isoformat() if b.generated_at else None,
                "risk_level": b.risk_level,
                "priority": b.priority,
                "status": "done" if b.pipeline_errors == 0 else "error",
                "deep_analysis_status": (b.full_report_json or {}).get("deep_analysis_status", "done"),
                "risk_score": (b.full_report_json or {}).get("risk_score"),
                "rul_hours": b.rul_hours,
            }
            for b in event.batches
        ]
        items.append(
            {
                "event_id": str(event.event_id),
                "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
                "triggered_by": event.triggered_by,
                "status": event.status,
                "machines_found": event.machines_found,
                "completed_at": event.completed_at.isoformat() if event.completed_at else None,
                "batch_count": len(batch_summaries),
                "batches": batch_summaries,
            }
        )

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": items,
    }


@router.get("/today")
async def list_todays_events(db: AsyncSession = Depends(get_db)) -> dict:
    """List ingestion events created today (UTC)."""
    today = datetime.now(timezone.utc).date()
    result = await db.execute(
        select(IngestionEvent)
        .where(func.date(IngestionEvent.triggered_at) == today)
        .order_by(IngestionEvent.triggered_at.desc())
        .options(selectinload(IngestionEvent.batches).selectinload(ReportBatch.machine))
    )
    events = result.scalars().all()

    items = []
    for event in events:
        batch_summaries = [
            {
                "batch_id": str(b.batch_id),
                "machine_id": str(b.machine_id),
                "generated_at": b.generated_at.isoformat() if b.generated_at else None,
                "risk_level": b.risk_level,
                "priority": b.priority,
            }
            for b in event.batches
        ]
        items.append(
            {
                "event_id": str(event.event_id),
                "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
                "status": event.status,
                "machines_found": event.machines_found,
                "batch_count": len(batch_summaries),
                "batches": batch_summaries,
            }
        )

    return {"date": today.isoformat(), "items": items}


@router.get("/event/{event_id}")
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Return one ingestion event with all of its report batches."""
    result = await db.execute(
        select(IngestionEvent)
        .where(IngestionEvent.event_id == event_id)
        .options(selectinload(IngestionEvent.batches).selectinload(ReportBatch.machine))
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    batches = [
        {
            "batch_id": str(b.batch_id),
            "machine_id": str(b.machine_id),
            "machine_name": b.machine.machine_name if b.machine else None,
            "generated_at": b.generated_at.isoformat() if b.generated_at else None,
            "root_cause": b.root_cause,
            "failure_mode": b.failure_mode,
            "confidence": b.confidence,
            "risk_level": b.risk_level,
            "recommended_action": b.recommended_action,
            "priority": b.priority,
            "rul_hours": b.rul_hours,
            "verification_passed": b.verification_passed,
            "pipeline_errors": b.pipeline_errors,
            "deep_analysis_status": (b.full_report_json or {}).get("deep_analysis_status", "done"),
            "risk_score": (b.full_report_json or {}).get("risk_score"),
        }
        for b in event.batches
    ]

    return {
        "event_id": str(event.event_id),
        "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
        "triggered_by": event.triggered_by,
        "status": event.status,
        "machines_found": event.machines_found,
        "completed_at": event.completed_at.isoformat() if event.completed_at else None,
        "batches": batches,
    }


@router.post("/deep-analyze/{machine_id}")
async def run_deep_analysis(
    machine_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enqueue a full deep-analysis job for one machine and return immediately.

    The actual multi-agent pipeline runs as a BackgroundTask. Use
    GET /reports/jobs/{job_id} to poll for status and progress.
    """
    # Find the most recent batch for this machine so we can reuse its event_id.
    existing_result = await db.execute(
        select(ReportBatch)
        .where(ReportBatch.machine_id == machine_id)
        .order_by(ReportBatch.generated_at.desc())
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        event_id = existing.event_id
    else:
        event_res = await db.execute(
            select(IngestionEvent).order_by(IngestionEvent.triggered_at.desc()).limit(1)
        )
        ev = event_res.scalar_one_or_none()
        if ev is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No ingestion events found — ingest data first.",
            )
        event_id = ev.event_id

    job = await deep_analysis_job_service.create_job(machine_id, event_id, db)
    background_tasks.add_task(
        execute_deep_analysis_job, job.job_id, machine_id, event_id
    )

    return {
        "job_id": str(job.job_id),
        "status": "queued",
    }


@router.get("/jobs/{job_id}")
async def get_deep_analysis_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the status and progress of a deep-analysis job."""
    job = await deep_analysis_job_service.get_job(job_id, db)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "current_stage": job.current_stage,
        "progress_percent": job.progress_percent,
        "machine_id": str(job.machine_id),
        "batch_id": str(job.batch_id) if job.batch_id else None,
        "error_message": job.error_message,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "duration_ms": job.duration_ms,
    }


@router.delete("/event/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    """Delete an ingestion event and cascade-delete all related batches/reports."""
    result = await db.execute(
        select(IngestionEvent).where(IngestionEvent.event_id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    await db.delete(event)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_batch_with_roles(
    batch_id: uuid.UUID, db: AsyncSession
) -> ReportBatch:
    result = await db.execute(
        select(ReportBatch)
        .where(ReportBatch.batch_id == batch_id)
        .options(selectinload(ReportBatch.role_reports))
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return batch


def _role_content(batch: ReportBatch, role: str) -> str:
    for rr in batch.role_reports:
        if rr.role == role:
            return rr.content
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{role} report not found for this batch",
    )


@router.get("/batch/{batch_id}")
async def get_batch(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Return the full report JSON for one batch."""
    batch = await _get_batch_with_roles(batch_id, db)
    return {
        "batch_id": str(batch.batch_id),
        "event_id": str(batch.event_id),
        "machine_id": str(batch.machine_id),
        "generated_at": batch.generated_at.isoformat() if batch.generated_at else None,
        "report": batch.full_report_json,
    }


@router.get("/batch/{batch_id}/engineer")
async def get_engineer_report(
    batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the stored engineer report text."""
    batch = await _get_batch_with_roles(batch_id, db)
    return {"role": "engineer", "content": _role_content(batch, "engineer")}


@router.get("/batch/{batch_id}/supervisor")
async def get_supervisor_report(
    batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the stored supervisor report text."""
    batch = await _get_batch_with_roles(batch_id, db)
    return {"role": "supervisor", "content": _role_content(batch, "supervisor")}


@router.get("/batch/{batch_id}/manager")
async def get_manager_report(
    batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    """Return the stored manager report text."""
    batch = await _get_batch_with_roles(batch_id, db)
    return {"role": "manager", "content": _role_content(batch, "manager")}


@router.get("/batch/{batch_id}/pdf")
async def get_role_pdf(
    batch_id: uuid.UUID,
    role: str = Query("engineer", pattern="^(engineer|supervisor|manager)$"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return a PDF rendering of the requested role report."""
    batch = await _get_batch_with_roles(batch_id, db)
    content = _role_content(batch, role)
    pdf_bytes = generate_pdf_from_batch(batch, role, content)

    filename = f"{role}_report_{batch_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
