import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ingested_file import IngestedFile
from app.schemas.upload_response import UploadResponse
from app.services.document_ingestion_service import (
    delete_document_file,
    ingest_manual,
    ingest_sop,
)
from app.services.machine_ingestion_service import (
    delete_machine_file,
    ingest_machines,
)
from app.services.maintenance_ingestion_service import (
    delete_maintenance_file,
    ingest_maintenance_records,
)
from app.services.sensor_ingestion_service import (
    delete_sensor_file,
    ingest_sensor_readings,
)

router = APIRouter(prefix="/upload", tags=["upload"])

_MAX_CSV_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_PDF_BYTES = 100 * 1024 * 1024  # 100 MB


def _require_csv(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only .csv files are accepted for this endpoint",
        )


def _require_pdf(file: UploadFile) -> None:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only .pdf files are accepted for this endpoint",
        )


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB",
        )
    return content


@router.post("/machines", response_model=UploadResponse)
async def upload_machines(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    _require_csv(file)
    content = await _read_limited(file, _MAX_CSV_BYTES)
    return await ingest_machines(content, file.filename, db)


@router.post("/sensors", response_model=UploadResponse)
async def upload_sensors(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    _require_csv(file)
    content = await _read_limited(file, _MAX_CSV_BYTES)
    return await ingest_sensor_readings(content, file.filename, db)


@router.post("/maintenance", response_model=UploadResponse)
async def upload_maintenance(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    _require_csv(file)
    content = await _read_limited(file, _MAX_CSV_BYTES)
    return await ingest_maintenance_records(content, file.filename, db)


@router.post("/manuals", response_model=UploadResponse)
async def upload_manuals(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    _require_pdf(file)
    content = await _read_limited(file, _MAX_PDF_BYTES)
    return await ingest_manual(content, file.filename, db)


@router.post("/sops", response_model=UploadResponse)
async def upload_sops(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    _require_pdf(file)
    content = await _read_limited(file, _MAX_PDF_BYTES)
    return await ingest_sop(content, file.filename, db)


@router.get("/files")
async def list_ingested_files(
    db: AsyncSession = Depends(get_db),
    file_type: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> dict:
    """List all uploaded files with their metadata."""
    stmt = select(IngestedFile).order_by(IngestedFile.uploaded_at.desc())
    if file_type:
        stmt = stmt.where(IngestedFile.file_type == file_type)

    count_result = await db.execute(
        select(IngestedFile.file_id).select_from(IngestedFile)
    )
    total = len(count_result.scalars().all())

    result = await db.execute(stmt.offset(skip).limit(limit))
    files = result.scalars().all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "files": [
            {
                "file_id": str(f.file_id),
                "ingestion_event_id": str(f.ingestion_event_id) if f.ingestion_event_id else None,
                "original_filename": f.original_filename,
                "file_type": f.file_type,
                "status": f.status,
                "row_count": f.row_count,
                "page_count": f.page_count,
                "machine_count": f.machine_count,
                "error_count": f.error_count,
                "storage_path": f.storage_path,
                "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
                "errors": f.errors,
            }
            for f in files
        ],
    }


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ingested_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a single uploaded file's metadata and stored copies."""
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file_id format",
        )

    result = await db.execute(select(IngestedFile).where(IngestedFile.file_id == fid))
    file_record = result.scalar_one_or_none()
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    deleted = False
    if file_record.file_type == "machines_csv":
        deleted = await delete_machine_file(db, file_record)
    elif file_record.file_type == "sensors_csv":
        deleted = await delete_sensor_file(db, file_record)
    elif file_record.file_type == "maintenance_csv":
        deleted = await delete_maintenance_file(db, file_record)
    elif file_record.file_type in ("manual_pdf", "sop_pdf"):
        deleted = await delete_document_file(db, file_record)
    else:
        await db.delete(file_record)
        await db.commit()
        deleted = True

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete file",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
