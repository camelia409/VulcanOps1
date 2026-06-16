import io
import uuid
from datetime import timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingested_file import IngestedFile
from app.models.machine import Machine
from app.models.sensor_reading import SensorReading
from app.schemas.upload_response import UploadResponse
from app.services.ingestion_file_tracker import update_ingested_file

STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage" / "uploads" / "sensor_history"

REQUIRED_COLUMNS = {"machine_id", "timestamp"}
NUMERIC_COLUMNS = {"temperature", "vibration", "pressure", "load", "rpm"}


def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


async def ingest_sensor_readings(
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

    # Cache valid machine IDs to avoid per-row DB queries
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

        # Validate timestamp
        raw_ts = str(row.get("timestamp", "")).strip()
        try:
            ts = pd.to_datetime(raw_ts, utc=True).to_pydatetime()
        except Exception:
            errors.append(f"Row {row_num}: cannot parse timestamp '{raw_ts}'")
            continue

        # Parse optional numeric sensor fields — non-numeric treated as null
        sensor_values: dict[str, float | None] = {}
        for col in NUMERIC_COLUMNS:
            raw = row.get(col, None)
            if pd.isna(raw) or str(raw).strip() == "":
                sensor_values[col] = None
            else:
                try:
                    sensor_values[col] = float(raw)
                except (ValueError, TypeError):
                    row_errors.append(
                        f"Row {row_num}: non-numeric value '{raw}' for '{col}', stored as null"
                    )
                    sensor_values[col] = None

        if row_errors:
            errors.extend(row_errors)

        db.add(
            SensorReading(
                machine_id=machine_id,
                timestamp=ts,
                temperature=sensor_values.get("temperature"),
                vibration=sensor_values.get("vibration"),
                pressure=sensor_values.get("pressure"),
                load=sensor_values.get("load"),
                rpm=sensor_values.get("rpm"),
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


async def delete_sensor_file(
    db: AsyncSession,
    file_record: IngestedFile,
) -> bool:
    """Delete a sensor file's stored copy. Parsed rows are kept for integrity."""
    if file_record.storage_path:
        try:
            Path(file_record.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(file_record)
    await db.commit()
    return True
