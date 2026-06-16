import io
import re
from pathlib import Path

import pdfplumber
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingested_file import IngestedFile
from app.schemas.upload_response import UploadResponse
from app.services.ingestion_file_tracker import update_ingested_file

STORAGE_ROOTS = {
    "manual": Path(__file__).resolve().parents[2] / "storage" / "uploads" / "manuals",
    "sop": Path(__file__).resolve().parents[2] / "storage" / "uploads" / "sops",
}


def _extract_text(pdf_bytes: bytes) -> str:
    lines: list[str] = []
    with pdfplumber.open(
        pdf_bytes if hasattr(pdf_bytes, "read") else io.BytesIO(pdf_bytes)
    ) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                lines.append(text)
    return "\n\n".join(lines)


def _safe_stem(filename: str) -> str:
    return re.sub(r"[^\w\-.]", "_", Path(filename).stem)


async def _ingest_document(
    content: bytes,
    filename: str,
    doc_type: str,
    db: AsyncSession,
    file_id: str | None = None,
) -> UploadResponse:
    storage_dir = STORAGE_ROOTS[doc_type]
    storage_dir.mkdir(parents=True, exist_ok=True)

    stem = _safe_stem(filename)
    pdf_path = storage_dir / f"{stem}.pdf"
    txt_path = storage_dir / f"{stem}.txt"

    pdf_path.write_bytes(content)

    try:
        extracted = _extract_text(content)
    except Exception as exc:
        if file_id:
            await update_ingested_file(
                db,
                file_id,
                status="error",
                storage_path=pdf_path,
                error_count=1,
                errors=[f"Text extraction failed: {exc}"],
            )
        return UploadResponse(
            status="error",
            rows_processed=1,
            rows_accepted=0,
            rows_rejected=1,
            errors=[f"Text extraction failed: {exc}"],
        )

    txt_path.write_text(extracted, encoding="utf-8")

    page_count = extracted.count("\n\n") + 1 if extracted.strip() else 0
    doc_errors: list[str] = (
        [] if extracted.strip() else ["PDF contained no extractable text"]
    )

    if file_id:
        await update_ingested_file(
            db,
            file_id,
            status="success",
            storage_path=pdf_path,
            extracted_text_path=txt_path,
            page_count=page_count,
            error_count=len(doc_errors),
            errors=doc_errors,
        )

    return UploadResponse(
        status="success",
        rows_processed=1,
        rows_accepted=1,
        rows_rejected=0,
        errors=doc_errors,
    )


async def ingest_manual(
    content: bytes,
    filename: str,
    db: AsyncSession,
    file_id: str | None = None,
) -> UploadResponse:
    return await _ingest_document(content, filename, "manual", db, file_id)


async def ingest_sop(
    content: bytes,
    filename: str,
    db: AsyncSession,
    file_id: str | None = None,
) -> UploadResponse:
    return await _ingest_document(content, filename, "sop", db, file_id)


async def delete_document_file(
    db: AsyncSession,
    file_record: IngestedFile,
) -> bool:
    """Delete a PDF and its extracted text."""
    if file_record.storage_path:
        try:
            Path(file_record.storage_path).unlink(missing_ok=True)
        except Exception:
            pass
    if file_record.extracted_text_path:
        try:
            Path(file_record.extracted_text_path).unlink(missing_ok=True)
        except Exception:
            pass
    await db.delete(file_record)
    await db.commit()
    return True
