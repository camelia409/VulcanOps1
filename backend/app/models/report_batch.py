import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ReportBatch(Base):
    __tablename__ = "report_batches"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "machine_id", name="uq_report_batch_event_machine"
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ingestion_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    machine_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("machines.machine_id"),
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    root_cause: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(String, nullable=True)
    priority: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rul_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_passed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    pipeline_errors: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    full_report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    event: Mapped["IngestionEvent"] = relationship(
        "IngestionEvent", back_populates="batches"
    )
    machine: Mapped["Machine"] = relationship(
        "Machine", lazy="selectin"
    )
    role_reports: Mapped[list["StoredRoleReport"]] = relationship(
        "StoredRoleReport", back_populates="batch", cascade="all, delete-orphan"
    )
