import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.core.enums import MachineCriticality, MachineStatus


class Machine(Base):
    __tablename__ = "machines"

    machine_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    machine_name: Mapped[str] = mapped_column(String(255), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(100), nullable=False)
    plant: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    criticality: Mapped[MachineCriticality] = mapped_column(
        Enum(MachineCriticality, name="machine_criticality"), nullable=False
    )
    status: Mapped[MachineStatus] = mapped_column(
        Enum(MachineStatus, name="machine_status"),
        nullable=False,
        default=MachineStatus.OPERATIONAL,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
