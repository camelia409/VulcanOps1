import uuid

from pydantic import BaseModel, Field

from app.core.enums import MachineCriticality, MachineStatus


class MachineSchema(BaseModel):
    machine_id: uuid.UUID
    machine_name: str = Field(..., min_length=1, max_length=255)
    machine_type: str = Field(..., min_length=1, max_length=100)
    plant: str = Field(..., min_length=1, max_length=255)
    location: str = Field(..., min_length=1, max_length=255)
    criticality: MachineCriticality
    status: MachineStatus

    model_config = {"from_attributes": True}


class MachineCreate(BaseModel):
    machine_name: str = Field(..., min_length=1, max_length=255)
    machine_type: str = Field(..., min_length=1, max_length=100)
    plant: str = Field(..., min_length=1, max_length=255)
    location: str = Field(..., min_length=1, max_length=255)
    criticality: MachineCriticality
    status: MachineStatus = MachineStatus.OPERATIONAL
