import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SparePart(Base):
    __tablename__ = "spare_parts"
    __table_args__ = (
        UniqueConstraint("part_name", "supplier", name="uq_spare_parts_name_supplier"),
    )

    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    part_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    supplier: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
