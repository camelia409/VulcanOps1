import io
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MachineCriticality, MachineStatus
from app.models.ingested_file import IngestedFile
from app.models.machine import Machine
from app.schemas.upload_response import UploadResponse
from app.services.ingestion_file_tracker import update_ingested_file

STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage" / "uploads" / "machine_registry"

REQUIRED_COLUMNS = {
    "machine_id",
    "machine_name",
    "machine_type",
    "plant",
    "location",
    "criticality",
    "status",
}

# Accept common aliases from the problem statement.
_COLUMN_ALIASES = {
    "name": "machine_name",
    "type": "machine_type",
}

VALID_CRITICALITY = {e.value for e in MachineCriticality}
VALID_STATUS = {e.value for e in MachineStatus}


def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [
        _COLUMN_ALIASES.get(c.strip().lower().replace(" ", "_"), c.strip().lower().replace(" ", "_"))
        for c in df.columns
    ]
    return df


async def ingest_machines(
    content: bytes,
    filename: str,
    db: AsyncSession,
    file_id: uuid.UUID | None = None,
) -> UploadResponse:
    errors: list[str] = []

    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        if file_id:
            await update_ingested_file(
                db,
                file_id,
                status="error",
                error_count=1,
                errors=[f"Could not parse CSV: {exc}"],
            )
        return UploadResponse(status="error", errors=[f"Could not parse CSV: {exc}"])

    df = _normalize_headers(df)

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        err = f"Missing required columns: {sorted(missing)}"
        if file_id:
            await update_ingested_file(
                db, file_id, status="error", error_count=1, errors=[err]
            )
        return UploadResponse(status="error", errors=[err])

    # Save raw file
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = STORAGE_DIR / filename
    storage_path.write_bytes(content)

    rows_processed = len(df)
    accepted = 0
    machine_count = 0

    for idx, row in df.iterrows():
        row_num = int(idx) + 2  # 1-based, accounting for header
        row_errors: list[str] = []

        # Validate enum fields
        criticality_val = str(row.get("criticality", "")).strip().lower()
        status_val = str(row.get("status", "")).strip().lower()

        if criticality_val not in VALID_CRITICALITY:
            row_errors.append(
                f"Row {row_num}: invalid criticality '{criticality_val}'. "
                f"Must be one of {sorted(VALID_CRITICALITY)}"
            )
        if status_val not in VALID_STATUS:
            row_errors.append(
                f"Row {row_num}: invalid status '{status_val}'. "
                f"Must be one of {sorted(VALID_STATUS)}"
            )

        # Validate required string fields are non-empty
        for field in ("machine_name", "machine_type", "plant", "location"):
            if not str(row.get(field, "")).strip():
                row_errors.append(f"Row {row_num}: '{field}' must not be empty")

        if row_errors:
            errors.extend(row_errors)
            continue

        # Parse machine_id — accept provided UUID or generate one
        raw_id = str(row.get("machine_id", "")).strip()
        try:
            machine_id = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            machine_id = uuid.uuid4()

        # Upsert: update if exists, insert if not
        result = await db.execute(select(Machine).where(Machine.machine_id == machine_id))
        existing = result.scalar_one_or_none()

        if existing:
            existing.machine_name = str(row["machine_name"]).strip()
            existing.machine_type = str(row["machine_type"]).strip()
            existing.plant = str(row["plant"]).strip()
            existing.location = str(row["location"]).strip()
            existing.criticality = MachineCriticality(criticality_val)
            existing.status = MachineStatus(status_val)
        else:
            db.add(
                Machine(
                    machine_id=machine_id,
                    machine_name=str(row["machine_name"]).strip(),
                    machine_type=str(row["machine_type"]).strip(),
                    plant=str(row["plant"]).strip(),
                    location=str(row["location"]).strip(),
                    criticality=MachineCriticality(criticality_val),
                    status=MachineStatus(status_val),
                )
            )
            machine_count += 1

        accepted += 1

    await db.commit()

    if file_id:
        await update_ingested_file(
            db,
            file_id,
            status="success" if not errors or accepted > 0 else "error",
            storage_path=storage_path,
            row_count=rows_processed,
            machine_count=machine_count,
            error_count=len(errors),
            errors=errors,
        )

    return UploadResponse(
        status="success" if not errors or accepted > 0 else "error",
        rows_processed=rows_processed,
        rows_accepted=accepted,
        rows_rejected=rows_processed - accepted,
        errors=errors,
    )


async def delete_machine_file(
    db: AsyncSession,
    file_record: IngestedFile,
) -> bool:
    """Delete a machine registry file's stored copy. Parsed rows are kept for integrity."""
    if file_record.storage_path:
        try:
            Path(file_record.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(file_record)
    await db.commit()
    return True
