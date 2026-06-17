"""Deep Analysis Execution Service — background runner for on-demand jobs.

This module is the bridge between the asynchronous job table and the existing
agent pipeline. It runs as a FastAPI BackgroundTask, guarantees every job
reaches a terminal state (done or failed), and reuses the existing report
persistence path so that GET /reports/batch/{batch_id} continues to work
unchanged.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.state_contract import VulcanOpsState
from app.db.session import AsyncSessionLocal
from app.models.report_batch import ReportBatch
from app.models.stored_role_report import StoredRoleReport
from app.orchestrator.pipeline_runner import PipelineError, run_pipeline
from app.services import deep_analysis_job_service, report_builder
from app.services.report_persistence_service import persist_batch

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")


async def _persist_deep_analysis_report(
    state: VulcanOpsState,
    event_id: uuid.UUID | None,
    db: AsyncSession,
) -> uuid.UUID:
    """Persist or update the report batch for a completed deep analysis.

    Mirrors the persistence behaviour previously inside the synchronous
    POST /reports/deep-analyze/{machine_id} endpoint:
      - Updates the most recent batch for the machine if one exists.
      - Otherwise creates a new batch linked to *event_id*.

    Returns the batch_id of the persisted report.
    """
    machine_id = state.active_machine_id
    report = report_builder.build_single_report(state)
    report["deep_analysis_status"] = "done"

    existing_result = await db.execute(
        select(ReportBatch)
        .where(ReportBatch.machine_id == machine_id)
        .options(selectinload(ReportBatch.role_reports))
        .order_by(ReportBatch.generated_at.desc())
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
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
        return existing.batch_id

    # No prior batch exists — create one.
    batch = await persist_batch(event_id, state, report, db)
    return batch.batch_id


async def _fail_existing_batch(machine_id: uuid.UUID, error: str, db: AsyncSession) -> None:
    """Mark the most recent batch for a machine as failed so the UI stays in sync."""
    result = await db.execute(
        select(ReportBatch)
        .where(ReportBatch.machine_id == machine_id)
        .options(selectinload(ReportBatch.role_reports))
        .order_by(ReportBatch.generated_at.desc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return

    existing.full_report_json = existing.full_report_json or {}
    existing.full_report_json["deep_analysis_status"] = "failed"
    existing.full_report_json["deep_analysis_error"] = error
    existing.pipeline_errors = (existing.pipeline_errors or 0) + 1
    existing.full_report_json["pipeline_errors"] = existing.pipeline_errors
    await db.commit()


async def _on_pipeline_stage(
    job_id: uuid.UUID,
    stage: str,
) -> None:
    """Progress callback forwarded from the agent graph to the job service."""
    async with AsyncSessionLocal() as db:
        try:
            await deep_analysis_job_service.update_stage(job_id, stage, db)
        except Exception:
            # A progress update must never break the pipeline.
            logger.exception(
                "Failed to update job %s progress for stage %s", job_id, stage
            )


async def execute_deep_analysis_job(
    job_id: uuid.UUID,
    machine_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> None:
    """Background task: run the pipeline and mark the job done or failed.

    Args:
        job_id:    The queued DeepAnalysisJob id.
        machine_id: Machine to analyze.
        event_id:   Optional parent ingestion event (used only when a new
                    batch must be created).
    """
    # Mark running.
    async with AsyncSessionLocal() as db:
        await deep_analysis_job_service.start_job(job_id, db)

    machine_id_str = str(machine_id)
    state: VulcanOpsState | None = None

    try:
        async with AsyncSessionLocal() as db:
            state = await asyncio.wait_for(
                run_pipeline(
                    machine_id_str,
                    db,
                    progress_callback=lambda stage: _on_pipeline_stage(job_id, stage),
                ),
                timeout=settings.DEEP_ANALYSIS_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        error_message = (
            f"Deep analysis timed out after {settings.DEEP_ANALYSIS_TIMEOUT_SECONDS}s"
        )
        _PIPELINE_LOG.error("Deep analysis job %s timed out", job_id)
        async with AsyncSessionLocal() as db:
            await deep_analysis_job_service.fail_job(job_id, error_message, db)
            await _fail_existing_batch(machine_id, error_message, db)
        return
    except PipelineError as exc:
        error_message = str(exc)
        _PIPELINE_LOG.error(
            "Deep analysis job %s pipeline error: %s", job_id, error_message
        )
        async with AsyncSessionLocal() as db:
            await deep_analysis_job_service.fail_job(job_id, error_message, db)
            await _fail_existing_batch(machine_id, error_message, db)
        return
    except Exception as exc:
        error_message = f"Pipeline failed: {exc}"
        _PIPELINE_LOG.exception("Deep analysis job %s failed", job_id)
        async with AsyncSessionLocal() as db:
            await deep_analysis_job_service.fail_job(job_id, error_message, db)
            await _fail_existing_batch(machine_id, error_message, db)
        return

    # Persist report and mark done.
    try:
        async with AsyncSessionLocal() as db:
            batch_id = await _persist_deep_analysis_report(state, event_id, db)
            await deep_analysis_job_service.complete_job(job_id, batch_id, db)
        _PIPELINE_LOG.info(
            "Deep analysis job %s completed -> batch %s", job_id, batch_id
        )
    except Exception as exc:
        error_message = f"Report persistence failed: {exc}"
        _PIPELINE_LOG.exception("Deep analysis job %s persistence failed", job_id)
        async with AsyncSessionLocal() as db:
            await deep_analysis_job_service.fail_job(job_id, error_message, db)
            await _fail_existing_batch(machine_id, error_message, db)
