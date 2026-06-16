import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SensorReadingSchema(BaseModel):
    machine_id: uuid.UUID
    timestamp: datetime
    temperature: float | None = Field(None, description="Degrees Celsius")
    vibration: float | None = Field(None, description="mm/s RMS")
    pressure: float | None = Field(None, description="Bar")
    load: float | None = Field(None, description="Percentage 0–100")
    rpm: float | None = Field(None, ge=0, description="Revolutions per minute")

    model_config = {"from_attributes": True}


class SensorReadingCreate(BaseModel):
    machine_id: uuid.UUID
    timestamp: datetime
    temperature: float | None = None
    vibration: float | None = None
    pressure: float | None = None
    load: float | None = None
    rpm: float | None = None
