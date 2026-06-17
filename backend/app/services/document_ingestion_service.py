"""Document ingestion service — PDF → text chunks → DB-backed semantic index.

Storage strategy (Phase 2 upgrade):
  Primary : document_chunks table in PostgreSQL with pgvector embeddings.
  Fallback : /storage/uploads/<type>/<stem>.txt kept as disk backup so the
             old keyword-based retrieval still works if pgvector is unavailable.

This means the system never loses documents on Render redeploys: the primary
store is the database, not the ephemeral local filesystem.
"""

import io
import json
import logging
import re
import uuid
from pathlib import Path

import pdfplumber
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_chunk import DocumentChunk
from app.models.ingested_file import IngestedFile
from app.schemas.upload_response import UploadResponse
from app.services import embedding_service
from app.services.ingestion_file_tracker import update_ingested_file

logger = logging.getLogger(__name__)

STORAGE_ROOTS = {
    "manual": Path(__file__).resolve().parents[2] / "storage" / "uploads" / "manuals",
    "sop": Path(__file__).resolve().parents[2] / "storage" / "uploads" / "sops",
}

_CHUNK_MIN_WORDS = 20
_CHUNK_MAX_WORDS = 120


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


def _split_chunks(text: str) -> list[str]:
    """Split extracted text into paragraph-level chunks within word-count bounds."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        words = para.split()
        if len(words) < _CHUNK_MIN_WORDS:
            continue
        if len(words) <= _CHUNK_MAX_WORDS:
            chunks.append(para)
        else:
            step = _CHUNK_MAX_WORDS // 2
            for i in range(0, len(words) - _CHUNK_MIN_WORDS, step):
                chunks.append(" ".join(words[i: i + _CHUNK_MAX_WORDS]))
    return chunks


async def _store_chunks_in_db(
    filename: str,
    source_type: str,
    chunks: list[str],
    db: AsyncSession,
) -> int:
    """Delete old chunks for this filename, embed new ones, persist to DB.

    Returns the number of chunks stored.
    """
    # Remove any previously ingested chunks for this file so re-uploads are clean.
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.source_filename == filename)
    )

    if not chunks:
        return 0

    # Embed all chunks in one batch call — more efficient than per-chunk calls.
    embeddings = await embedding_service.embed_batch(chunks)

    for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
        # Serialize embedding list to JSON string; compatible with both TEXT and
        # pgvector columns (pgvector accepts '[0.1, ...]'::vector text literals).
        embedding_str = json.dumps(embedding) if embedding is not None else None
        db.add(DocumentChunk(
            chunk_id=uuid.uuid4(),
            source_filename=filename,
            source_type=source_type,
            chunk_index=i,
            chunk_text=chunk_text,
            embedding=embedding_str,
        ))

    await db.flush()
    return len(chunks)


async def _ingest_document(
    content: bytes,
    filename: str,
    doc_type: str,
    db: AsyncSession,
    file_id: str | None = None,
) -> UploadResponse:
    # ── PDF extraction ─────────────────────────────────────────────────────────
    try:
        extracted = _extract_text(content)
    except Exception as exc:
        if file_id:
            await update_ingested_file(
                db, file_id,
                status="error",
                error_count=1,
                errors=[f"Text extraction failed: {exc}"],
            )
        return UploadResponse(
            status="error",
            rows_processed=1, rows_accepted=0, rows_rejected=1,
            errors=[f"Text extraction failed: {exc}"],
        )

    doc_errors: list[str] = []
    if not extracted.strip():
        doc_errors.append("PDF contained no extractable text")

    # ── Disk backup (kept for local-dev keyword fallback) ──────────────────────
    stem = _safe_stem(filename)
    try:
        storage_dir = STORAGE_ROOTS[doc_type]
        storage_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = storage_dir / f"{stem}.pdf"
        txt_path = storage_dir / f"{stem}.txt"
        pdf_path.write_bytes(content)
        if extracted.strip():
            txt_path.write_text(extracted, encoding="utf-8")
    except Exception as exc:
        logger.warning("Disk backup write failed (non-fatal): %s", exc)
        pdf_path = None
        txt_path = None

    # ── DB-primary chunk storage with embeddings ───────────────────────────────
    chunks = _split_chunks(extracted) if extracted.strip() else []
    chunks_stored = 0
    try:
        chunks_stored = await _store_chunks_in_db(filename, doc_type, chunks, db)
        logger.info(
            "Stored %d chunks for %s (%s) in document_chunks table",
            chunks_stored, filename, doc_type,
        )
    except Exception as exc:
        logger.warning("Chunk DB storage failed (non-fatal): %s", exc)
        doc_errors.append(f"Semantic index update failed: {exc}")

    page_count = extracted.count("\n\n") + 1 if extracted.strip() else 0

    if file_id:
        await update_ingested_file(
            db, file_id,
            status="success" if not doc_errors else "error",
            storage_path=pdf_path,
            extracted_text_path=txt_path,
            page_count=page_count,
            error_count=len(doc_errors),
            errors=doc_errors,
        )

    return UploadResponse(
        status="success",
        rows_processed=1,
        rows_accepted=chunks_stored or 1,
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
    """Delete all chunks for this document from the DB and remove disk backup files."""
    filename = file_record.original_filename or ""
    if filename:
        try:
            await db.execute(
                delete(DocumentChunk).where(DocumentChunk.source_filename == filename)
            )
            await db.flush()
        except Exception as exc:
            logger.warning("Chunk deletion failed for %s: %s", filename, exc)

    # Best-effort disk cleanup.
    for path_attr in ("storage_path", "extracted_text_path"):
        raw = getattr(file_record, path_attr, None)
        if raw:
            try:
                Path(raw).unlink(missing_ok=True)
            except Exception:
                pass

    await db.delete(file_record)
    await db.commit()
    return True
