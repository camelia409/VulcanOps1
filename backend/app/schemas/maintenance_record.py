import uuid
from datetime import date

from pydantic import BaseModel, Field


class MaintenanceRecordSchema(BaseModel):
    maintenance_id: uuid.UUID
    machine_id: uuid.UUID
    date: date
    failure_mode: str = Field(..., min_length=1, max_length=255)
    action_taken: str = Field(..., min_length=1)
    downtime_hours: float = Field(..., ge=0)
    engineer: str = Field(..., min_length=1, max_length=255)

    model_config = {"from_attributes": True}


class MaintenanceRecordCreate(BaseModel):
    machine_id: uuid.UUID
    date: date
    failure_mode: str = Field(..., min_length=1, max_length=255)
    action_taken: str = Field(..., min_length=1)
    downtime_hours: float = Field(..., ge=0)
    engineer: str = Field(..., min_length=1, max_length=255)
