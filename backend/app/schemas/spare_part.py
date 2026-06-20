from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class SparePartSchema(BaseModel):
    part_id: uuid.UUID
    part_name: str
    category: str | None = None
    qty_on_hand: int = 0
    reorder_threshold: int = 0
    lead_time_days: int
    unit_cost_usd: Decimal | None = None
    supplier: str | None = None
    last_updated: datetime | None = None

    model_config = {"from_attributes": True}


class SparePartCreate(BaseModel):
    part_name: str
    category: str | None = None
    qty_on_hand: int = Field(0, ge=0)
    reorder_threshold: int = Field(0, ge=0)
    lead_time_days: int = Field(..., ge=1)
    unit_cost_usd: Decimal | None = None
    supplier: str | None = None
