import io
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingested_file import IngestedFile
from app.models.machine import Machine
from app.models.maintenance_record import MaintenanceRecord
from app.schemas.upload_response import UploadResponse
from app.services.ingestion_file_tracker import update_ingested_file

STORAGE_DIR = (
    Path(__file__).resolve().parents[2] / "storage" / "uploads" / "maintenance_history"
)

# Relaxed from the full column set so PS files with machine_id + failure_mode are accepted.
REQUIRED_COLUMNS = {"machine_id", "failure_mode"}


def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


async def ingest_maintenance_records(
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

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    storage_path = STORAGE_DIR / filename
    storage_path.write_bytes(content)

    rows_processed = len(df)
    accepted = 0
    valid_machine_ids: set[uuid.UUID] = set()

    for idx, row in df.iterrows():
        row_num = int(idx) + 2
        row_errors: list[str] = []

        # Validate machine_id
        raw_id = str(row.get("machine_id", "")).strip()
        try:
            machine_id = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            errors.append(f"Row {row_num}: invalid machine_id '{raw_id}'")
            continue

        if machine_id not in valid_machine_ids:
            result = await db.execute(
                select(Machine.machine_id).where(Machine.machine_id == machine_id)
            )
            if result.scalar_one_or_none() is None:
                errors.append(f"Row {row_num}: machine_id '{machine_id}' does not exist")
                continue
            valid_machine_ids.add(machine_id)

        # Validate date if present; default to today if missing.
        raw_date = str(row.get("date", "")).strip()
        if raw_date:
            try:
                parsed_date = pd.to_datetime(raw_date).date()
            except Exception:
                errors.append(f"Row {row_num}: cannot parse date '{raw_date}'")
                continue
        else:
            parsed_date = pd.Timestamp.now().date()

        # Validate downtime_hours if present.
        downtime = 0.0
        raw_downtime = row.get("downtime_hours")
        if raw_downtime is not None and str(raw_downtime).strip() != "":
            try:
                downtime = float(raw_downtime)
                if downtime < 0:
                    raise ValueError("negative")
            except (ValueError, TypeError):
                errors.append(
                    f"Row {row_num}: 'downtime_hours' must be a non-negative number"
                )
                continue

        # Validate required text fields
        for field in ("failure_mode", "action_taken", "engineer"):
            if field in df.columns and not str(row.get(field, "")).strip():
                row_errors.append(f"Row {row_num}: '{field}' must not be empty")

        if row_errors:
            errors.extend(row_errors)
            continue

        # Parse optional maintenance_id
        raw_mid = str(row.get("maintenance_id", "")).strip()
        try:
            maintenance_id = uuid.UUID(raw_mid)
        except (ValueError, AttributeError):
            maintenance_id = uuid.uuid4()

        db.add(
            MaintenanceRecord(
                maintenance_id=maintenance_id,
                machine_id=machine_id,
                date=parsed_date,
                failure_mode=str(row["failure_mode"]).strip(),
                action_taken=str(row.get("action_taken", "")).strip(),
                downtime_hours=downtime,
                engineer=str(row.get("engineer", "")).strip(),
            )
        )
        accepted += 1

    await db.commit()

    if file_id:
        await update_ingested_file(
            db,
            file_id,
            status="success" if accepted > 0 else "error",
            storage_path=storage_path,
            row_count=rows_processed,
            error_count=len(errors),
            errors=errors,
        )

    return UploadResponse(
        status="success" if accepted > 0 else "error",
        rows_processed=rows_processed,
        rows_accepted=accepted,
        rows_rejected=rows_processed - accepted,
        errors=errors,
    )


async def delete_maintenance_file(
    db: AsyncSession,
    file_record: IngestedFile,
) -> bool:
    """Delete a maintenance file's stored copy. Parsed rows are kept for integrity."""
    if file_record.storage_path:
        try:
            Path(file_record.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(file_record)
    await db.commit()
    return True
