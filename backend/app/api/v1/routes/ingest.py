"""
Ingest Route — single endpoint that accepts mixed CSV/PDF uploads and triggers
an autonomous pipeline run for every machine found in the registry.

This endpoint returns immediately (HTTP 202) and continues processing in a
FastAPI BackgroundTask so large uploads with many machines do not time out.

File type detection is content/header based so users do not have to follow a
rigid naming convention.
"""

import csv
import io
import uuid

import pdfplumber
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ingested_file import IngestedFile
from app.models.ingestion_event import IngestionEvent
from app.models.machine import Machine
from app.models.report_batch import ReportBatch
from app.schemas.upload_response import UploadResponse
from app.services.document_ingestion_service import ingest_manual, ingest_sop
from app.services.ingest_orchestration_service import run_autonomous_pipeline
from app.services.ingestion_file_tracker import create_ingested_file, update_ingested_file
from app.services.machine_ingestion_service import ingest_machines
from app.services.maintenance_ingestion_service import ingest_maintenance_records
from app.services.sensor_ingestion_service import ingest_sensor_readings

router = APIRouter(prefix="/ingest", tags=["ingest"])

_MAX_CSV_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB


# ── content-based file classification ──────────────────────────────────────────

# Machine registry: machine_id + (name or machine_name) + (type or machine_type) + plant
_CSV_MACHINE_REQUIRED = {"machine_id"}
_CSV_MACHINE_NAME_ALIASES = {"machine_name", "name"}
_CSV_MACHINE_TYPE_ALIASES = {"machine_type", "type"}
_CSV_MACHINE_OTHER = {"plant"}

_CSV_SENSOR_REQUIRED = {"machine_id", "timestamp"}

# Maintenance: machine_id + failure_mode (relaxed from full column set)
_CSV_MAINTENANCE_REQUIRED = {"machine_id", "failure_mode"}

_SOP_KEYWORDS = [
    "sop",
    "standard operating procedure",
    "procedure",
    "emergency",
    "threshold",
    "inspection required",
    "immediate inspection",
]


def _normalize_header(header: str) -> str:
    return header.strip().lower().replace(" ", "_")


def _csv_headers(content: bytes) -> set[str] | None:
    """Return the normalized header set for a CSV byte blob, or None on parse failure."""
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return None
    sample = text.splitlines()[0] if text else ""
    if not sample:
        return None
    try:
        reader = csv.reader(io.StringIO(sample))
        headers = next(reader, [])
        return {_normalize_header(h) for h in headers if h.strip()}
    except Exception:
        return None


def _classify_csv(content: bytes) -> str | None:
    headers = _csv_headers(content)
    if not headers:
        return None

    has_machine_id = "machine_id" in headers
    has_name = bool(_CSV_MACHINE_NAME_ALIASES & headers)
    has_type = bool(_CSV_MACHINE_TYPE_ALIASES & headers)
    has_plant = "plant" in headers

    if has_machine_id and has_name and has_type and has_plant:
        return "machines_csv"

    if _CSV_SENSOR_REQUIRED.issubset(headers):
        return "sensors_csv"

    if _CSV_MAINTENANCE_REQUIRED.issubset(headers):
        return "maintenance_csv"

    return None


def _extract_pdf_text_sample(content: bytes, max_chars: int = 4000) -> str:
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            chunks: list[str] = []
            for page in pdf.pages[:3]:
                text = page.extract_text()
                if text:
                    chunks.append(text)
                    if sum(len(c) for c in chunks) >= max_chars:
                        break
            return "\n".join(chunks)[:max_chars].lower()
    except Exception:
        return ""


def _classify_pdf(content: bytes, filename: str) -> str | None:
    name_lower = (filename or "").lower()
    if "sop" in name_lower:
        return "sop_pdf"

    text = _extract_pdf_text_sample(content)
    if any(kw in text for kw in _SOP_KEYWORDS):
        return "sop_pdf"

    return "manual_pdf"


def _classify_file(filename: str | None, content: bytes) -> str | None:
    """Return a classifier string for a file by inspecting its content, or None."""
    if not filename:
        return None
    name_lower = filename.lower()

    if name_lower.endswith(".csv"):
        return _classify_csv(content)

    if name_lower.endswith(".pdf"):
        return _classify_pdf(content, filename)

    return None


# ── request helpers ────────────────────────────────────────────────────────────


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File '{file.filename}' exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB",
        )
    return content


async def _route_file(
    file_type: str,
    content: bytes,
    filename: str,
    db: AsyncSession,
    file_id: uuid.UUID,
) -> UploadResponse:
    if file_type == "machines_csv":
        return await ingest_machines(content, filename, db, file_id)
    if file_type == "sensors_csv":
        return await ingest_sensor_readings(content, filename, db, file_id)
    if file_type == "maintenance_csv":
        return await ingest_maintenance_records(content, filename, db, file_id)
    if file_type == "sop_pdf":
        return await ingest_sop(content, filename, db, file_id)
    if file_type == "manual_pdf":
        return await ingest_manual(content, filename, db, file_id)
    raise ValueError(f"Unhandled file type: {file_type}")


# ── endpoints ──────────────────────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def ingest_files(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="CSV or PDF files to ingest"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Upload one or more CSV/PDF files and start autonomous report generation.

    Returns a 202 Accepted response with the event_id that can be used to poll
    the reports endpoints.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Create the ingestion event up front so the client has an event_id.
    event = IngestionEvent(status="running")
    db.add(event)
    await db.flush()

    file_summaries: list[dict] = []
    errors: list[str] = []

    for file in files:
        filename = file.filename or "unnamed"
        try:
            max_bytes = _MAX_PDF_BYTES if filename.lower().endswith(".pdf") else _MAX_CSV_BYTES
            content = await _read_limited(file, max_bytes)
        except HTTPException:
            raise
        except Exception as exc:
            errors.append(f"'{filename}': could not read file ({exc})")
            file_summaries.append(
                {
                    "name": filename,
                    "type": "unknown",
                    "rows": 0,
                    "status": "error",
                    "errors": [str(exc)],
                }
            )
            continue

        file_type = _classify_file(filename, content)

        if file_type is None:
            errors.append(f"'{filename}': unsupported file type")
            file_summaries.append(
                {
                    "name": filename,
                    "type": "unknown",
                    "rows": 0,
                    "status": "rejected",
                    "errors": ["unsupported file type"],
                }
            )
            continue

        # Create the per-file tracking record before processing.
        file_record = await create_ingested_file(
            db,
            ingestion_event_id=event.event_id,
            original_filename=filename,
            file_type=file_type,
        )

        try:
            result = await _route_file(file_type, content, filename, db, file_record.file_id)

            file_summaries.append(
                {
                    "file_id": str(file_record.file_id),
                    "name": filename,
                    "type": file_type,
                    "rows": result.rows_processed or 0,
                    "status": result.status,
                    "errors": result.errors,
                }
            )
            if result.errors:
                errors.extend(f"'{filename}': {e}" for e in result.errors)
        except HTTPException:
            raise
        except Exception as exc:
            errors.append(f"'{filename}': {exc}")
            file_summaries.append(
                {
                    "file_id": str(file_record.file_id),
                    "name": filename,
                    "type": file_type,
                    "rows": 0,
                    "status": "error",
                    "errors": [str(exc)],
                }
            )
            # Mark the file record as failed.
            await update_ingested_file(
                db,
                file_record.file_id,
                status="error",
                error_count=1,
                errors=[str(exc)],
            )

    # Discover all machines now present in the registry.
    machine_result = await db.execute(select(Machine.machine_id))
    machine_ids: list[uuid.UUID] = list(machine_result.scalars().all())

    event.files_uploaded = file_summaries
    event.machines_found = len(machine_ids)
    # Transition: file ingestion finished, agent pipeline about to start.
    event.status = "processing"
    await db.commit()

    # Trigger background processing for every machine.
    background_tasks.add_task(
        run_autonomous_pipeline,
        event_id=event.event_id,
        machine_ids=machine_ids,
    )

    return {
        "event_id": str(event.event_id),
        "status": event.status,
        "machines_found": event.machines_found,
        "files": file_summaries,
        "errors": errors if errors else None,
    }


@router.get("/status")
async def ingest_status(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Return the latest ingestion event status plus a pipeline summary.
    """
    event_result = await db.execute(
        select(IngestionEvent).order_by(IngestionEvent.triggered_at.desc()).limit(1)
    )
    event = event_result.scalar_one_or_none()

    if event is None:
        return {
            "event_id": None,
            "status": "pending",
            "machines_queued": 0,
            "reports_generated": 0,
            "errors": 0,
        }

    reports_result = await db.execute(
        select(
            func.count(ReportBatch.batch_id).label("generated"),
            func.coalesce(func.sum(ReportBatch.pipeline_errors), 0).label("errors"),
        ).where(ReportBatch.event_id == event.event_id)
    )
    row = reports_result.one_or_none()
    reports_generated = row.generated if row else 0
    error_count = int(row.errors or 0) if row else 0

    if event.status == "failed" and error_count == 0:
        error_count = event.machines_found

    return {
        "event_id": str(event.event_id),
        "status": event.status,
        "machines_queued": event.machines_found,
        "reports_generated": reports_generated,
        "errors": error_count,
    }
