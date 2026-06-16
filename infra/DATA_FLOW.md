# VulcanOps — Industrial Data Flow

This document describes the canonical data flow through the VulcanOps reliability
pipeline. It is the authoritative reference for sequencing agent stages and defining
what data is available at each decision point.

---

## Pipeline Overview

```
Upload / Input
      │
      │  CSV sensor export, maintenance log upload,
      │  manual machine registration, or real-time
      │  sensor push via API.
      │
      ▼
Machine Context
      │
      │  Resolve active_machine_id → load MachineSchema.
      │  Hydrate: machine_type, plant, location,
      │  criticality, status.
      │  Gate: if machine not found → add error, halt.
      │
      ▼
Evidence
      │
      │  1. Load SensorReadings for the machine
      │     (time-windowed from InfluxDB / PostgreSQL).
      │  2. Load MaintenanceHistory from PostgreSQL.
      │  3. Retrieve any additional structured evidence
      │     (manuals, specs, prior reports) into
      │     retrieved_evidence[].
      │
      ▼
Analysis
      │
      │  Run sequentially, each result written to state:
      │
      │  a. Anomaly Detection
      │     → state.anomaly (AnomalyDetail)
      │
      │  b. Remaining Useful Life Prediction
      │     → state.rul_prediction (RULPrediction)
      │
      │  c. Diagnosis
      │     → state.diagnosis (DiagnosisResult)
      │
      │  d. Verification
      │     Cross-checks diagnosis against evidence.
      │     → state.verification (VerificationResult)
      │
      ▼
Decision
      │
      │  a. Impact Assessment
      │     Risk level, cost, production impact,
      │     compliance flags.
      │     → state.impact (ImpactAssessment)
      │
      │  b. Strategy Selection
      │     Recommended action, parts, priority,
      │     repair estimate, safety notes.
      │     → state.strategy (StrategyDecision)
      │     → state.priority (MaintenancePriority)
      │
      ▼
Role Reports
      │
      │  Three role-scoped views generated from state:
      │
      │  ┌─────────────────────────────────────────────┐
      │  │ EngineerReport  — repair steps, parts,      │
      │  │                   safety, repair hours       │
      │  ├─────────────────────────────────────────────┤
      │  │ SupervisorReport — downtime, resources,     │
      │  │                    production line impact    │
      │  ├─────────────────────────────────────────────┤
      │  │ ManagerReport  — cost, compliance, business │
      │  │                   impact, strategic summary  │
      │  └─────────────────────────────────────────────┘
      │
      │  → state.role_reports (RoleReports)
      │  → state.final_report (persisted to DB as GeneratedReport)
      │
      ▼
     End
```

---

## State Progression Table

| Stage          | Fields Written                                           |
|----------------|----------------------------------------------------------|
| Input          | `active_machine_id`                                      |
| Machine Context| `machine_context`                                        |
| Evidence       | `sensor_readings`, `maintenance_history`, `retrieved_evidence` |
| Analysis       | `anomaly`, `rul_prediction`, `diagnosis`, `verification` |
| Decision       | `impact`, `strategy`, `priority`                         |
| Role Reports   | `role_reports`, `final_report`                           |
| Throughout     | `llm_telemetry`, `errors`                                |

---

## Error Handling Contract

- Any stage that encounters a recoverable error appends to `state.errors[]`
  and continues with degraded output.
- Any stage that encounters a blocking error appends to `state.errors[]`
  and halts the pipeline. Downstream fields remain `None`.
- `state.errors` is always inspected before persisting `final_report`.
  Reports with blocking errors are flagged and not surfaced to role views.

---

## Data Ownership

| Store      | Owns                                        |
|------------|---------------------------------------------|
| PostgreSQL | Machines, MaintenanceRecords, GeneratedReports |
| InfluxDB   | SensorReadings (time-series, high-frequency) |
| Redis      | Pipeline task queue, short-lived cache       |
