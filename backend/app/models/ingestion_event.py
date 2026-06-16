import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    triggered_by: Mapped[str] = mapped_column(
        String(50), nullable=False, default="user"
    )
    files_uploaded: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    machines_found: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    files: Mapped[list["IngestedFile"]] = relationship(
        "IngestedFile", back_populates="event"
    )
    batches: Mapped[list["ReportBatch"]] = relationship(
        "ReportBatch", back_populates="event", cascade="all, delete-orphan"
    )
