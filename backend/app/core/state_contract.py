"""
Canonical shared state for every agent in the VulcanOps reliability pipeline.

Every agent reads from and writes to this single object. No agent holds private
state that another agent needs. Fields are appended to as the pipeline progresses;
no field is overwritten by a later stage without explicit intent.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import MaintenancePriority, RiskLevel
from app.schemas.machine import MachineSchema
from app.schemas.maintenance_record import MaintenanceRecordSchema
from app.schemas.report import EngineerReport, ManagerReport, SupervisorReport
from app.schemas.sensor_reading import SensorReadingSchema


class AnomalyDetail(BaseModel):
    detected: bool
    sensor: str | None = None
    value: float | None = None
    threshold: float | None = None
    deviation_percent: float | None = None
    detected_at: datetime | None = None


class RULPrediction(BaseModel):
    remaining_useful_life_hours: float | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    basis: str | None = None


class DiagnosisResult(BaseModel):
    root_cause: str | None = None
    failure_mode: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    verified: bool
    verification_notes: str | None = None
    contradictions: list[str] = Field(default_factory=list)
    evidence_score: float = Field(0.0, ge=0.0, le=1.0)
    history_score: float = Field(0.0, ge=0.0, le=1.0)
    combined_score: float = Field(0.0, ge=0.0, le=1.0)


class ImpactAssessment(BaseModel):
    risk_level: RiskLevel | None = None
    estimated_downtime_hours: float | None = Field(None, ge=0)
    estimated_cost_usd: float | None = Field(None, ge=0)
    affected_production_lines: list[str] = Field(default_factory=list)
    compliance_flags: list[str] = Field(default_factory=list)
    business_impact_summary: str | None = None


class StrategyDecision(BaseModel):
    recommended_action: str | None = None
    priority: MaintenancePriority | None = None
    estimated_repair_hours: float | None = Field(None, ge=0)
    parts_required: list[str] = Field(default_factory=list)
    safety_notes: str | None = None
    resource_requirements: str | None = None


class RoleReports(BaseModel):
    engineer: EngineerReport | None = None
    supervisor: SupervisorReport | None = None
    manager: ManagerReport | None = None


class LLMTelemetry(BaseModel):
    model: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: float = 0.0
    calls: list[dict[str, Any]] = Field(default_factory=list)


class VulcanOpsState(BaseModel):
    """
    Single shared state object passed through the entire agent pipeline.

    Populated progressively: each agent fills its designated fields and passes
    the state forward. Fields left as None indicate that stage has not yet run.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    active_machine_id: uuid.UUID

    # ── Stage 1: Context loading ──────────────────────────────────────────────
    machine_context: MachineSchema | None = None

    # ── Stage 2: Sensor data ──────────────────────────────────────────────────
    sensor_readings: list[SensorReadingSchema] = Field(default_factory=list)

    # ── Stage 3: Historical evidence ──────────────────────────────────────────
    maintenance_history: list[MaintenanceRecordSchema] = Field(default_factory=list)
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)

    # ── Stage 4: Analysis ─────────────────────────────────────────────────────
    anomaly: AnomalyDetail | None = None
    rul_prediction: RULPrediction | None = None
    diagnosis: DiagnosisResult | None = None
    verification: VerificationResult | None = None

    # ── Stage 5: Decision ─────────────────────────────────────────────────────
    impact: ImpactAssessment | None = None
    strategy: StrategyDecision | None = None
    priority: MaintenancePriority | None = None

    # ── Stage 6: Output ───────────────────────────────────────────────────────
    role_reports: RoleReports = Field(default_factory=RoleReports)
    final_report: dict[str, Any] | None = None

    # ── Observability ─────────────────────────────────────────────────────────
    llm_telemetry: LLMTelemetry = Field(default_factory=LLMTelemetry)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
