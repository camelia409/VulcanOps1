"""Tests for the asynchronous deep-analysis job system."""

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import deep_analysis_job_service
from app.services.deep_analysis_execution_service import execute_deep_analysis_job


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_db() -> MagicMock:
    """Return a minimal AsyncSession mock that supports the service API."""
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock(return_value=AsyncMock())
    return db


def _mock_result_row(row=None):
    """Build an AsyncMock result whose scalar_one_or_none returns *row*."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=row)
    return result


def _make_job(**overrides):
    job = MagicMock()
    job.job_id = overrides.get("job_id", uuid.uuid4())
    job.machine_id = overrides.get("machine_id", uuid.uuid4())
    job.event_id = overrides.get("event_id", uuid.uuid4())
    job.batch_id = overrides.get("batch_id", None)
    job.status = overrides.get("status", "queued")
    job.current_stage = overrides.get("current_stage", "queued")
    job.progress_percent = overrides.get("progress_percent", 0)
    job.error_message = overrides.get("error_message", None)
    job.queued_at = overrides.get("queued_at", None)
    job.started_at = overrides.get("started_at", None)
    job.completed_at = overrides.get("completed_at", None)
    job.duration_ms = overrides.get("duration_ms", None)
    return job


# ── service unit tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_job_queued():
    db = _make_mock_db()
    db.execute.return_value = _mock_result_row(row=None)

    machine_id = uuid.uuid4()
    event_id = uuid.uuid4()

    job = await deep_analysis_job_service.create_job(machine_id, event_id, db)

    assert job.status == "queued"
    assert job.current_stage == "queued"
    assert job.progress_percent == 0
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_job_running():
    db = _make_mock_db()
    job = _make_job(status="queued")
    db.execute.return_value = _mock_result_row(row=job)

    result = await deep_analysis_job_service.start_job(job.job_id, db)

    assert result.status == "running"
    assert result.current_stage == "load_machine"
    assert result.started_at is not None


@pytest.mark.asyncio
async def test_update_stage_progress_mapping():
    db = _make_mock_db()
    job = _make_job(status="running")
    db.execute.return_value = _mock_result_row(row=job)

    await deep_analysis_job_service.update_stage(job.job_id, "diagnosis_agent", db)

    assert job.current_stage == "diagnosis_agent"
    assert job.progress_percent == 50
    assert job.status == "running"


@pytest.mark.asyncio
async def test_complete_job_terminal_done():
    db = _make_mock_db()
    job = _make_job(status="running", started_at=MagicMock())
    db.execute.return_value = _mock_result_row(row=job)

    batch_id = uuid.uuid4()
    result = await deep_analysis_job_service.complete_job(job.job_id, batch_id, db)

    assert result.status == "done"
    assert result.progress_percent == 100
    assert result.batch_id == batch_id
    assert result.completed_at is not None
    assert result.duration_ms is not None


@pytest.mark.asyncio
async def test_fail_job_terminal_failed():
    db = _make_mock_db()
    job = _make_job(status="running", current_stage="diagnosis_agent")
    db.execute.return_value = _mock_result_row(row=job)

    result = await deep_analysis_job_service.fail_job(
        job.job_id, "Something went wrong", db
    )

    assert result.status == "failed"
    assert result.error_message == "Something went wrong"
    assert result.completed_at is not None


# ── execution service tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_job_success():
    job = _make_job(status="queued")
    machine_id = uuid.uuid4()
    event_id = uuid.uuid4()
    batch_id = uuid.uuid4()

    mock_state = MagicMock()
    mock_state.active_machine_id = machine_id

    async def _fake_run_pipeline(machine_id, db, *, progress_callback=None):
        if progress_callback:
            await progress_callback("anomaly_engine")
            await progress_callback("finalize_report")
        return mock_state

    with patch(
        "app.services.deep_analysis_execution_service.run_pipeline",
        side_effect=_fake_run_pipeline,
    ):
        with patch(
            "app.services.deep_analysis_execution_service._persist_deep_analysis_report",
            new=AsyncMock(return_value=batch_id),
        ):
            db = _make_mock_db()
            db.execute.return_value = _mock_result_row(row=job)
            with patch(
                "app.services.deep_analysis_execution_service.AsyncSessionLocal"
            ) as mock_session_factory:
                mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
                mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
                await execute_deep_analysis_job(job.job_id, machine_id, event_id)

    assert job.status == "done"
    assert job.batch_id == batch_id
    assert job.progress_percent == 100


@pytest.mark.asyncio
async def test_execute_job_failure_terminates_failed():
    job = _make_job(status="queued")
    machine_id = uuid.uuid4()
    event_id = uuid.uuid4()

    async def _fake_run_pipeline(machine_id, db, *, progress_callback=None):
        raise RuntimeError("LLM unreachable")

    with patch(
        "app.services.deep_analysis_execution_service.run_pipeline",
        side_effect=_fake_run_pipeline,
    ):
        db = _make_mock_db()
        db.execute.return_value = _mock_result_row(row=job)
        with patch(
            "app.services.deep_analysis_execution_service.AsyncSessionLocal"
        ) as mock_session_factory:
            mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await execute_deep_analysis_job(job.job_id, machine_id, event_id)

    assert job.status == "failed"
    assert "LLM unreachable" in job.error_message
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_execute_job_timeout_terminates_failed():
    """A pipeline that hangs must be forced to a failed terminal state."""
    job = _make_job(status="queued")
    machine_id = uuid.uuid4()
    event_id = uuid.uuid4()

    async def _fake_run_pipeline(machine_id, db, *, progress_callback=None):
        await asyncio.sleep(10)
        return MagicMock()

    with patch(
        "app.services.deep_analysis_execution_service.run_pipeline",
        side_effect=_fake_run_pipeline,
    ):
        with patch(
            "app.services.deep_analysis_execution_service.settings"
        ) as mock_settings:
            mock_settings.DEEP_ANALYSIS_TIMEOUT_SECONDS = 0.01
            db = _make_mock_db()
            db.execute.return_value = _mock_result_row(row=job)
            with patch(
                "app.services.deep_analysis_execution_service.AsyncSessionLocal"
            ) as mock_session_factory:
                mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=db)
                mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
                await execute_deep_analysis_job(job.job_id, machine_id, event_id)

    assert job.status == "failed"
    assert "timed out" in job.error_message.lower()
    assert job.completed_at is not None


# ── route unit tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_deep_analyze_returns_immediately():
    """The endpoint must create a job and return before any pipeline work."""
    from app.api.v1.routes.reports import run_deep_analysis

    machine_id = uuid.uuid4()
    event_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Mock DB: no existing batch, latest event found.
    db = _make_mock_db()
    event_mock = MagicMock()
    event_mock.event_id = event_id
    db.execute.side_effect = [
        _mock_result_row(row=None),  # existing batch
        _mock_result_row(row=event_mock),  # latest ingestion event
    ]

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    fake_job = _make_job(job_id=job_id, status="queued")

    with patch.object(
        deep_analysis_job_service, "create_job", new=AsyncMock(return_value=fake_job)
    ):
        t0 = time.monotonic()
        response = await run_deep_analysis(machine_id, background_tasks, db=db)
        elapsed_ms = (time.monotonic() - t0) * 1000

    assert response == {"job_id": str(job_id), "status": "queued"}
    assert elapsed_ms < 1000
    background_tasks.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_get_job_endpoint_returns_progress():
    from app.api.v1.routes.reports import get_deep_analysis_job

    job_id = uuid.uuid4()
    fake_job = _make_job(
        job_id=job_id,
        status="running",
        current_stage="communication_formatter",
        progress_percent=95,
    )

    db = _make_mock_db()
    with patch.object(
        deep_analysis_job_service, "get_job", new=AsyncMock(return_value=fake_job)
    ):
        response = await get_deep_analysis_job(job_id, db=db)

    assert response["job_id"] == str(job_id)
    assert response["status"] == "running"
    assert response["current_stage"] == "communication_formatter"
    assert response["progress_percent"] == 95


@pytest.mark.asyncio
async def test_get_job_endpoint_404_for_missing_job():
    from app.api.v1.routes.reports import get_deep_analysis_job
    from fastapi import HTTPException

    db = _make_mock_db()
    with patch.object(
        deep_analysis_job_service, "get_job", new=AsyncMock(return_value=None)
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_deep_analysis_job(uuid.uuid4(), db=db)
        assert exc_info.value.status_code == 404


# ── progress callback wiring ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pipeline_progress_callback_invoked():
    """The progress callback is forwarded through run_pipeline into graph nodes."""
    from app.orchestrator.pipeline_runner import run_pipeline
    from app.core.state_contract import VulcanOpsState
    from app.schemas.machine import MachineSchema
    from app.core.enums import MachineCriticality, MachineStatus

    progress_stages = []

    async def _fake_progress(stage):
        progress_stages.append(stage)

    fake_state = VulcanOpsState(
        active_machine_id=uuid.uuid4(),
        machine_context=MachineSchema(
            machine_id=uuid.uuid4(),
            machine_name="Pump-01",
            machine_type="pump",
            plant="Alpha",
            location="Line 1",
            criticality=MachineCriticality.HIGH,
            status=MachineStatus.OPERATIONAL,
        ),
    )

    async def _fake_ainvoke(state):
        # Simulate a few node wrappers firing.
        from app.orchestrator.graph_builder import _progress_callback_ctx

        cb = _progress_callback_ctx.get()
        if cb:
            await cb("anomaly_engine")
            await cb("diagnosis_agent")
            await cb("finalize_report")
        return fake_state

    mock_graph = MagicMock()
    mock_graph.ainvoke = _fake_ainvoke

    db = _make_mock_db()

    with patch("app.orchestrator.pipeline_runner.get_graph", return_value=mock_graph):
        with patch(
            "app.orchestrator.pipeline_runner._load_machine",
            new=AsyncMock(return_value=fake_state.machine_context),
        ):
            with patch(
                "app.orchestrator.pipeline_runner._load_sensor_readings",
                new=AsyncMock(return_value=[]),
            ):
                with patch(
                    "app.orchestrator.pipeline_runner._load_maintenance_history",
                    new=AsyncMock(return_value=[]),
                ):
                    await run_pipeline(
                        str(uuid.uuid4()), db, progress_callback=_fake_progress
                    )

    assert progress_stages == ["anomaly_engine", "diagnosis_agent", "finalize_report"]
