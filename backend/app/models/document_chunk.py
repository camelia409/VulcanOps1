"""DocumentChunk — a single text passage from an ingested manual or SOP.

The `embedding` column is TEXT in both environments:
  - Local PostgreSQL (no pgvector): stored as JSON string, keyword search fallback.
  - Neon (pgvector): stored as JSON string, cast to vector on the fly in raw SQL
    via '<text>::vector' — pgvector accepts text-format vector literals.

Retrieval agent uses raw SQL with the <=> operator; any ORM-level type issue is
caught and falls back to keyword search automatically.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored as JSON string "[0.1, 0.2, ...]" compatible with both TEXT and vector columns.
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
