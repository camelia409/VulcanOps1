import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatMessage(Base):
    """
    Persisted chat turn for the Industrial Copilot.

    Each row stores one assistant response and the user query that produced it,
    keeping a simple, scrollable conversation history in the UI.
    """

    __tablename__ = "chat_messages"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="assistant"
    )
    query: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    response_json: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now(), nullable=False
    )
