import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EngineerFeedback(Base):
    __tablename__ = "engineer_feedback"
    __table_args__ = (
        UniqueConstraint(
            "report_batch_id", "engineer_id",
            name="uq_engineer_feedback_batch_engineer",
        ),
        CheckConstraint(
            "thumbs IN ('up', 'down') OR thumbs IS NULL",
            name="ck_engineer_feedback_thumbs",
        ),
        CheckConstraint(
            "verdict IN ('correct', 'partial', 'wrong') OR verdict IS NULL",
            name="ck_engineer_feedback_verdict",
        ),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    report_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    machine_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    failure_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbs: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    engineer_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
