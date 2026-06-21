"""Deep Analysis Job Service — lifecycle management for asynchronous jobs.

Public API:
    create_job(machine_id, event_id, db) -> DeepAnalysisJob
    start_job(job_id, db) -> DeepAnalysisJob
    update_stage(job_id, stage, db) -> DeepAnalysisJob
    complete_job(job_id, batch_id, db) -> DeepAnalysisJob
    fail_job(job_id, error, db) -> DeepAnalysisJob
    get_job(job_id, db) -> DeepAnalysisJob | None

The service emits structured logs on every transition so external observers
can monitor job progress without polling the database directly.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.deep_analysis_job import DeepAnalysisJob

logger = logging.getLogger(__name__)
_JOB_LOG = logging.getLogger("vulcanops.pipeline")

# Pipeline stage -> user-visible progress percent.
_STAGE_PROGRESS = {
    "anomaly_engine": 10,
    "prognostics_engine": 20,
    "evidence_retrieval_agent": 30,
    "diagnosis_agent": 50,
    "evidence_verification_agent": 60,
    "operational_impact_engine": 70,
    "maintenance_strategy_agent": 80,
    "plant_priority_engine": 90,
    "communication_formatter": 95,
    "finalize_report": 100,
}

# Stages that are expected graph nodes but are not in the explicit map default
# to the previous known progress value; 0 is safe for anything before anomaly.
_DEFAULT_PROGRESS = 0


class JobNotFoundError(Exception):
    """Raised when a requested job_id does not exist."""


def _log_job_event(
    job: "DeepAnalysisJob",
    stage: str,
    status: str,
) -> None:
    """Emit the structured deep_analysis_job log required by the spec."""
    _JOB_LOG.info(
        json.dumps(
            {
                "event": "deep_analysis_job",
                "job_id": str(job.job_id),
                "machine_id": str(job.machine_id),
                "stage": stage,
                "status": status,
                "progress": job.progress_percent,
            }
        )
    )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _progress_for_stage(stage: str) -> int:
    return _STAGE_PROGRESS.get(stage, _DEFAULT_PROGRESS)


async def _get_job_row(
    job_id: uuid.UUID, db: AsyncSession
) -> "DeepAnalysisJob":
    from app.models.deep_analysis_job import DeepAnalysisJob

    result = await db.execute(
        select(DeepAnalysisJob).where(DeepAnalysisJob.job_id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise JobNotFoundError(f"Deep analysis job {job_id} not found")
    return job


async def create_job(
    machine_id: uuid.UUID,
    event_id: uuid.UUID | None,
    db: AsyncSession,
) -> "DeepAnalysisJob":
    """Create a queued deep-analysis job and return it."""
    from app.models.deep_analysis_job import DeepAnalysisJob

    job = DeepAnalysisJob(
        machine_id=machine_id,
        event_id=event_id,
        status="queued",
        current_stage="queued",
        progress_percent=0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    _log_job_event(job, stage="queued", status="queued")
    logger.info(
        "Created deep-analysis job %s for machine %s (event %s)",
        job.job_id,
        machine_id,
        event_id,
    )
    return job


async def start_job(
    job_id: uuid.UUID,
    db: AsyncSession,
) -> "DeepAnalysisJob":
    """Mark a job as running and record its start time."""
    job = await _get_job_row(job_id, db)
    job.status = "running"
    job.started_at = _now_utc()
    job.current_stage = "load_machine"
    job.progress_percent = 0
    await db.commit()
    await db.refresh(job)

    _log_job_event(job, stage="load_machine", status="running")
    logger.info("Started deep-analysis job %s", job_id)
    return job


async def update_stage(
    job_id: uuid.UUID,
    stage: str,
    db: AsyncSession,
) -> "DeepAnalysisJob":
    """Update the current pipeline stage and progress percent."""
    job = await _get_job_row(job_id, db)
    job.current_stage = stage
    job.progress_percent = _progress_for_stage(stage)
    # Ensure the job stays in running while stages advance.
    if job.status != "done" and job.status != "failed":
        job.status = "running"
    await db.commit()
    await db.refresh(job)

    _log_job_event(job, stage=stage, status=job.status)
    logger.debug(
        "Deep-analysis job %s stage=%s progress=%s",
        job_id,
        stage,
        job.progress_percent,
    )
    return job


async def complete_job(
    job_id: uuid.UUID,
    batch_id: uuid.UUID,
    db: AsyncSession,
) -> "DeepAnalysisJob":
    """Mark a job as done, linking the produced report batch."""
    job = await _get_job_row(job_id, db)
    completed_at = _now_utc()
    duration_ms = None
    if job.started_at is not None:
        duration_ms = int((completed_at - job.started_at).total_seconds() * 1000)

    job.status = "done"
    job.completed_at = completed_at
    job.duration_ms = duration_ms
    job.batch_id = batch_id
    job.current_stage = "finalize_report"
    job.progress_percent = 100
    await db.commit()
    await db.refresh(job)

    _log_job_event(job, stage="finalize_report", status="done")
    logger.info(
        "Completed deep-analysis job %s -> batch %s in %s ms",
        job_id,
        batch_id,
        duration_ms,
    )
    return job


async def fail_job(
    job_id: uuid.UUID,
    error: str,
    db: AsyncSession,
) -> "DeepAnalysisJob":
    """Mark a job as failed with a terminal error message."""
    job = await _get_job_row(job_id, db)
    completed_at = _now_utc()
    duration_ms = None
    if job.started_at is not None:
        duration_ms = int((completed_at - job.started_at).total_seconds() * 1000)

    job.status = "failed"
    job.completed_at = completed_at
    job.duration_ms = duration_ms
    job.error_message = error[:2000]  # cap length for safety
    job.current_stage = job.current_stage or "unknown"
    await db.commit()
    await db.refresh(job)

    _log_job_event(job, stage=job.current_stage or "unknown", status="failed")
    logger.error(
        "Failed deep-analysis job %s after %s ms: %s",
        job_id,
        duration_ms,
        error,
    )
    return job


async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession,
) -> "DeepAnalysisJob | None":
    """Return a job by id, or None if it does not exist."""
    from app.models.deep_analysis_job import DeepAnalysisJob

    result = await db.execute(
        select(DeepAnalysisJob).where(DeepAnalysisJob.job_id == job_id)
    )
    return result.scalar_one_or_none()
