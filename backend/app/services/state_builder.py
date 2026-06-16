"""
State Builder — pre-flight validation before the VulcanOps agent pipeline runs.

Validates that required data exists in the database and storage before committing
to a full pipeline execution. Returns a structured PreflightResult; raises
PreflightError on hard failures that make the pipeline pointless to run.

Checkpoints (run before every pipeline invocation):
    1. (HARD) Machines registered  — registry is not empty
    2. (HARD) Machine exists        — the specific machine_id is in the registry
    3. (HARD) Sensor data uploaded  — at least one reading exists for this machine
    4. (SOFT) Maintenance history   — at least one record exists for this machine
    5. (SOFT) Documents uploaded    — at least one .txt file in storage/uploads/

Post-pipeline checkpoints (5 & 6 from spec) are evaluated in integration_service.py
after the graph returns, because they depend on agent outputs.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.machine import Machine
from app.models.maintenance_record import MaintenanceRecord
from app.models.sensor_reading import SensorReading

# Must match the storage path used by evidence_retrieval_agent and upload routes
_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "storage" / "uploads"


class PreflightError(Exception):
    """Raised when a HARD checkpoint fails — pipeline must not proceed."""


@dataclass
class CheckpointStatus:
    passed: bool
    detail: str
    count: int = 0


@dataclass
class PreflightResult:
    passed: bool
    checkpoints: dict[str, CheckpointStatus] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _count_documents() -> int:
    """Count extracted .txt files under storage/uploads/manuals/ and /sops/."""
    total = 0
    for subdir in ("manuals", "sops"):
        d = _STORAGE_ROOT / subdir
        if d.is_dir():
            total += len(list(d.glob("*.txt")))
    return total


async def run_preflight(machine_id: str, db: AsyncSession) -> PreflightResult:
    """
    Run all pre-pipeline validation checkpoints for a given machine.

    Args:
        machine_id: UUID string of the machine to investigate.
        db:         Active async SQLAlchemy session.

    Returns:
        PreflightResult — all checkpoints passed (caller may inspect warnings).

    Raises:
        ValueError:     If machine_id is not a valid UUID.
        PreflightError: If any HARD checkpoint fails.
    """
    try:
        mid = uuid.UUID(machine_id)
    except ValueError as exc:
        raise ValueError(f"Invalid machine_id format: '{machine_id}'") from exc

    checkpoints: dict[str, CheckpointStatus] = {}
    warnings: list[str] = []
    errors: list[str] = []

    # ── Checkpoint 1 (HARD): registry non-empty ───────────────────────────────
    registry_count: int = (
        await db.execute(select(func.count()).select_from(Machine))
    ).scalar_one()

    cp1 = CheckpointStatus(
        passed=registry_count > 0,
        detail=f"{registry_count} machine(s) in registry",
        count=registry_count,
    )
    checkpoints["machines_uploaded"] = cp1
    if not cp1.passed:
        msg = "Checkpoint 1 FAILED: No machines in registry — upload machine_registry.csv first."
        raise PreflightError(msg)

    # ── Checkpoint 2 (HARD): specific machine exists ──────────────────────────
    machine_exists: int = (
        await db.execute(
            select(func.count()).select_from(Machine).where(Machine.machine_id == mid)
        )
    ).scalar_one()

    if machine_exists == 0:
        msg = f"Checkpoint 2 FAILED: Machine '{machine_id}' not found in registry."
        raise PreflightError(msg)

    # ── Checkpoint 3 (HARD): sensor readings exist for this machine ───────────
    sensor_count: int = (
        await db.execute(
            select(func.count())
            .select_from(SensorReading)
            .where(SensorReading.machine_id == mid)
        )
    ).scalar_one()

    cp3 = CheckpointStatus(
        passed=sensor_count > 0,
        detail=f"{sensor_count} sensor reading(s) for this machine",
        count=sensor_count,
    )
    checkpoints["sensor_data_uploaded"] = cp3
    if not cp3.passed:
        msg = (
            f"Checkpoint 3 FAILED: No sensor readings for machine '{machine_id}'. "
            "Upload sensor_history.csv first."
        )
        raise PreflightError(msg)

    # ── Checkpoint 4 (SOFT): maintenance history ──────────────────────────────
    maint_count: int = (
        await db.execute(
            select(func.count())
            .select_from(MaintenanceRecord)
            .where(MaintenanceRecord.machine_id == mid)
        )
    ).scalar_one()

    cp4 = CheckpointStatus(
        passed=maint_count > 0,
        detail=f"{maint_count} maintenance record(s) for this machine",
        count=maint_count,
    )
    checkpoints["maintenance_history_uploaded"] = cp4
    if not cp4.passed:
        warnings.append(
            "Checkpoint 4 WARNING: No maintenance history found for this machine. "
            "Evidence retrieval quality will be reduced. Upload maintenance_history.csv."
        )

    # ── Checkpoint 5 (SOFT): documents in storage ─────────────────────────────
    doc_count = _count_documents()
    cp5 = CheckpointStatus(
        passed=doc_count > 0,
        detail=f"{doc_count} document(s) available in storage",
        count=doc_count,
    )
    checkpoints["documents_uploaded"] = cp5
    if not cp5.passed:
        warnings.append(
            "Checkpoint 5 WARNING: No manuals or SOPs found in storage. "
            "Evidence retrieval will return no documentary evidence. Upload PDF files."
        )

    return PreflightResult(
        passed=True,
        checkpoints=checkpoints,
        warnings=warnings,
        errors=errors,
    )


async def get_system_status(db: AsyncSession) -> dict[str, Any]:
    """
    Return global data-availability status (not machine-specific).
    Called by GET /api/v1/investigate/status for the SystemStatus component.
    """
    machine_count: int = (
        await db.execute(select(func.count()).select_from(Machine))
    ).scalar_one()

    sensor_total: int = (
        await db.execute(select(func.count()).select_from(SensorReading))
    ).scalar_one()

    maint_total: int = (
        await db.execute(select(func.count()).select_from(MaintenanceRecord))
    ).scalar_one()

    doc_count = _count_documents()

    return {
        "checkpoints": {
            "machines_uploaded": {
                "passed": machine_count > 0,
                "count": machine_count,
                "detail": f"{machine_count} machine(s) registered",
            },
            "sensor_data_uploaded": {
                "passed": sensor_total > 0,
                "count": sensor_total,
                "detail": f"{sensor_total} total sensor reading(s)",
            },
            "maintenance_history_uploaded": {
                "passed": maint_total > 0,
                "count": maint_total,
                "detail": f"{maint_total} maintenance record(s)",
            },
            "documents_uploaded": {
                "passed": doc_count > 0,
                "count": doc_count,
                "detail": f"{doc_count} document(s) in storage",
            },
        },
    }
