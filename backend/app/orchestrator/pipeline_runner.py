"""
Pipeline runner — the single public entry point for the VulcanOps agent pipeline.

    state = await run_pipeline(machine_id="...", db=db_session)

Responsibilities:
    1. Validate machine_id and load Machine from PostgreSQL
    2. Load last N SensorReadings ordered by timestamp
    3. Load all MaintenanceRecords for the machine
    4. Construct initial VulcanOpsState
    5. Invoke the compiled LangGraph
    6. Return the final VulcanOpsState

Does NOT write to the database. Callers persist the report if required.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_contract import VulcanOpsState
from app.models.machine import Machine
from app.models.maintenance_record import MaintenanceRecord
from app.models.sensor_reading import SensorReading
from app.orchestrator.graph_builder import _progress_callback_ctx, get_graph
from app.schemas.machine import MachineSchema
from app.schemas.maintenance_record import MaintenanceRecordSchema
from app.schemas.sensor_reading import SensorReadingSchema

logger = logging.getLogger(__name__)
_PIPELINE_LOG = logging.getLogger("vulcanops.pipeline")

# Number of most-recent sensor readings loaded into the pipeline
_SENSOR_WINDOW = 200


def _log_step(event: str, machine_id: str, step: str, status: str, duration_ms: float | None = None, error: str | None = None) -> None:
    payload: dict[str, Any] = {
        "event": event,
        "machine_id": machine_id,
        "step": step,
        "status": status,
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 1)
    if error is not None:
        payload["error"] = error
    _PIPELINE_LOG.info(json.dumps(payload))


class PipelineError(Exception):
    """Raised when the pipeline cannot start due to a pre-condition failure."""


async def _load_machine(machine_id: uuid.UUID, db: AsyncSession) -> MachineSchema:
    result = await db.execute(
        select(Machine).where(Machine.machine_id == machine_id)
    )
    machine = result.scalar_one_or_none()
    if machine is None:
        raise PipelineError(f"Machine '{machine_id}' not found in registry")
    return MachineSchema.model_validate(machine)


async def _load_sensor_readings(
    machine_id: uuid.UUID, db: AsyncSession
) -> list[SensorReadingSchema]:
    result = await db.execute(
        select(SensorReading)
        .where(SensorReading.machine_id == machine_id)
        .order_by(desc(SensorReading.timestamp))
        .limit(_SENSOR_WINDOW)
    )
    rows = result.scalars().all()
    # Return in ascending time order so agents see a natural time series
    schemas = [SensorReadingSchema.model_validate(r) for r in reversed(rows)]
    return schemas


async def _load_maintenance_history(
    machine_id: uuid.UUID, db: AsyncSession
) -> list[MaintenanceRecordSchema]:
    result = await db.execute(
        select(MaintenanceRecord)
        .where(MaintenanceRecord.machine_id == machine_id)
        .order_by(desc(MaintenanceRecord.date))
    )
    rows = result.scalars().all()
    return [MaintenanceRecordSchema.model_validate(r) for r in rows]


async def run_pipeline(
    machine_id: str,
    db: AsyncSession,
    *,
    progress_callback: Callable[[str], Awaitable[None]] | None = None,
) -> VulcanOpsState:
    """
    Execute the full VulcanOps agent pipeline for a given machine.

    Args:
        machine_id:        UUID string of the target machine.
        db:                Active async SQLAlchemy session.
        progress_callback: Optional async callable(stage_name) that receives
                           the name of each graph node as it begins.

    Returns:
        VulcanOpsState with all agent outputs populated.

    Raises:
        PipelineError: if machine_id is invalid or the machine does not exist.
        ValueError:    if machine_id is not a valid UUID string.
    """
    try:
        mid = uuid.UUID(machine_id)
    except ValueError:
        raise ValueError(f"Invalid machine_id format: '{machine_id}'")

    machine_id_str = str(mid)
    _log_step("deep_analysis", machine_id_str, "load_machine", "start")
    t0 = time.monotonic()
    machine_context = await _load_machine(mid, db)
    _log_step("deep_analysis", machine_id_str, "load_machine", "end", duration_ms=(time.monotonic() - t0) * 1000)

    _log_step("deep_analysis", machine_id_str, "load_sensor_data", "start")
    t0 = time.monotonic()
    sensor_readings = await _load_sensor_readings(mid, db)
    _log_step("deep_analysis", machine_id_str, "load_sensor_data", "end", duration_ms=(time.monotonic() - t0) * 1000)

    _log_step("deep_analysis", machine_id_str, "load_maintenance", "start")
    t0 = time.monotonic()
    maintenance_history = await _load_maintenance_history(mid, db)
    _log_step("deep_analysis", machine_id_str, "load_maintenance", "end", duration_ms=(time.monotonic() - t0) * 1000)

    initial_state = VulcanOpsState(
        active_machine_id=mid,
        machine_context=machine_context,
        sensor_readings=sensor_readings,
        maintenance_history=maintenance_history,
    )

    graph = get_graph()

    _log_step("deep_analysis", machine_id_str, "agent_graph", "start")
    t0 = time.monotonic()

    # Inject the optional progress callback into the graph node context so
    # each agent stage can report its start without touching VulcanOpsState.
    progress_token = _progress_callback_ctx.set(progress_callback)
    try:
        # LangGraph returns either a dict or the Pydantic model depending on version;
        # normalise to VulcanOpsState either way.
        raw_result: Any = await graph.ainvoke(initial_state)
    finally:
        _progress_callback_ctx.reset(progress_token)

    graph_ms = (time.monotonic() - t0) * 1000
    _log_step("deep_analysis", machine_id_str, "agent_graph", "end", duration_ms=graph_ms)

    if isinstance(raw_result, VulcanOpsState):
        return raw_result

    # LangGraph returned a plain dict — reconstruct the model
    return VulcanOpsState(**raw_result)
