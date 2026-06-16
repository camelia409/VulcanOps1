import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import MaintenancePriority, RiskLevel


class ReportSchema(BaseModel):
    report_id: uuid.UUID
    machine_id: uuid.UUID
    generated_at: datetime
    root_cause: str
    risk_level: RiskLevel
    confidence: float = Field(..., ge=0.0, le=1.0)
    recommended_action: str

    model_config = {"from_attributes": True}


# ── Role-scoped report views ──────────────────────────────────────────────────

class EngineerReport(BaseModel):
    """Operational detail for the field engineer executing the repair."""

    report_id: uuid.UUID
    machine_id: uuid.UUID
    generated_at: datetime
    root_cause: str
    recommended_action: str
    risk_level: RiskLevel
    confidence: float = Field(..., ge=0.0, le=1.0)
    priority: MaintenancePriority
    estimated_repair_hours: float = Field(..., ge=0)
    parts_required: list[str]
    safety_notes: str


class SupervisorReport(BaseModel):
    """Operational overview for the shift supervisor coordinating resources."""

    report_id: uuid.UUID
    machine_id: uuid.UUID
    generated_at: datetime
    risk_level: RiskLevel
    priority: MaintenancePriority
    recommended_action: str
    estimated_downtime_hours: float = Field(..., ge=0)
    affected_production_lines: list[str]
    resource_requirements: str


class ManagerReport(BaseModel):
    """Strategic summary for plant management and reporting."""

    report_id: uuid.UUID
    machine_id: uuid.UUID
    generated_at: datetime
    risk_level: RiskLevel
    root_cause: str
    business_impact: str
    estimated_cost_usd: float = Field(..., ge=0)
    recommended_action: str
    compliance_flags: list[str]
