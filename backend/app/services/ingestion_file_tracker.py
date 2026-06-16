"""
Helper to create and update IngestedFile records during ingestion.

Services call these helpers so the route does not need to know internal storage
paths or row counts.
"""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingested_file import IngestedFile


async def create_ingested_file(
    db: AsyncSession,
    *,
    ingestion_event_id: uuid.UUID | None,
    original_filename: str,
    file_type: str,
) -> IngestedFile:
    """Create a pending IngestedFile row and flush to obtain file_id."""
    record = IngestedFile(
        ingestion_event_id=ingestion_event_id,
        original_filename=original_filename,
        file_type=file_type,
        status="pending",
    )
    db.add(record)
    await db.flush()
    return record


async def update_ingested_file(
    db: AsyncSession,
    file_id: uuid.UUID,
    *,
    status: str,
    storage_path: str | Path | None = None,
    extracted_text_path: str | Path | None = None,
    row_count: int | None = None,
    page_count: int | None = None,
    machine_count: int | None = None,
    error_count: int | None = None,
    errors: list[str] | None = None,
) -> None:
    """Update an IngestedFile row with processing results."""
    result = await db.execute(
        select(IngestedFile).where(IngestedFile.file_id == file_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        return

    record.status = status
    if storage_path is not None:
        record.storage_path = str(storage_path)
    if extracted_text_path is not None:
        record.extracted_text_path = str(extracted_text_path)
    if row_count is not None:
        record.row_count = row_count
    if page_count is not None:
        record.page_count = page_count
    if machine_count is not None:
        record.machine_count = machine_count
    if error_count is not None:
        record.error_count = error_count
    if errors is not None:
        record.errors = errors

    await db.commit()
