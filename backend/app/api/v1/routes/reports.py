"""
Reports Route — query and download persisted pipeline reports.

All endpoints operate on the tables created by the ingestion event flow:
`ingestion_events`, `report_batches`, and `stored_role_reports`.
"""

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.ingestion_event import IngestionEvent
from app.models.machine import Machine
from app.models.report_batch import ReportBatch
from app.models.stored_role_report import StoredRoleReport
from app.services.pdf_service import generate_pdf_from_batch

router = APIRouter(prefix="/reports", tags=["reports"])

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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Run the full AI pipeline for one machine and persist the result.

    If a batch already exists for this machine (fast-only or previous deep run),
    it is updated in place. Role reports are regenerated from the new pipeline output.

    This endpoint is intentionally synchronous — the pipeline takes 20-40 seconds.
    The frontend shows a loading state while waiting.
    """
    from app.orchestrator.pipeline_runner import PipelineError, run_pipeline
    from app.services import report_builder

    # Find the most recent batch for this machine so we can reuse its event_id.
    existing_result = await db.execute(
        select(ReportBatch)
        .where(ReportBatch.machine_id == machine_id)
        .options(selectinload(ReportBatch.role_reports))
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

    try:
        state = await run_pipeline(str(machine_id), db)
    except PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline failed: {exc}",
        )

    report = report_builder.build_single_report(state)
    report["deep_analysis_status"] = "done"

    if existing:
        # Update the existing batch in place (UniqueConstraint prevents a second row).
        existing.root_cause = report.get("root_cause")
        existing.failure_mode = report.get("failure_mode")
        existing.confidence = report.get("diagnosis_confidence")
        existing.risk_level = report.get("risk_level")
        existing.recommended_action = report.get("recommended_action")
        existing.priority = report.get("priority")
        existing.rul_hours = report.get("rul_hours")
        existing.verification_passed = (report.get("verification") or {}).get("verified")
        existing.pipeline_errors = report.get("pipeline_errors", 0)
        existing.full_report_json = report

        # Replace role reports.
        for rr in list(existing.role_reports):
            await db.delete(rr)
        await db.flush()

        for role in ("engineer", "supervisor", "manager"):
            db.add(
                StoredRoleReport(
                    batch_id=existing.batch_id,
                    role=role,
                    content=report.get(f"{role}_report") or "",
                )
            )
        await db.commit()
        return_id = existing.batch_id
    else:
        from app.services.report_persistence_service import persist_batch
        new_batch = await persist_batch(event_id, state, report, db)
        return_id = new_batch.batch_id

    return {
        "batch_id": str(return_id),
        "machine_id": str(machine_id),
        "deep_analysis_status": "done",
        "message": "Deep analysis complete.",
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
