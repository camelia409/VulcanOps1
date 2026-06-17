// ── Shared types used across the VulcanOps frontend ───────────────────────────

export interface TraceItem {
  agent_name: string;
  start_time: string;
  end_time: string;
  latency_ms: number;
  status: string;
  skip_reason?: string;
}

export interface MachineInfo {
  machine_id: string;
  machine_name: string;
  machine_type: string;
  plant: string;
  location: string;
  criticality: string;
  status: string;
}

export interface ReportData {
  machine: MachineInfo;
  risk_level: string | null;
  root_cause: string | null;
  failure_mode: string | null;
  diagnosis_confidence: number | null;
  recommended_action: string | null;
  priority: string | null;
  rul_hours: number | null;
  estimated_downtime_hours: number | null;
  estimated_cost_usd: number | null;
  parts_required: string[];
  anomaly: {
    detected: boolean;
    sensor: string | null;
    value: number | null;
    threshold: number | null;
    deviation_percent: number | null;
  } | null;
  verification: {
    verified: boolean;
    verification_notes: string | null;
  } | null;
  engineer_report: string | null;
  supervisor_report: string | null;
  manager_report: string | null;
  execution_trace: TraceItem[];
  pipeline_errors: number;
  has_errors: boolean;
  error?: string;
  evidence_chain?: EvidenceChainItem[];
  explainability_score?: number | null;
  procurement_gap?: ProcurementGapInfo | null;
}

export interface EvidenceChainItem {
  step: number;
  type: "sensor" | "history" | "manual";
  source: string;
  evidence: string;
}

export interface ProcurementGapInfo {
  procurement_gap: boolean;
  rul_days?: number | null;
  at_risk_parts?: {
    part: string;
    lead_time_days: number;
    rul_days: number;
    gap_days: number;
  }[];
  recommended_action?: string;
}

export interface PlantOverview {
  total_machines: number;
  emergency_count: number;
  urgent_count: number;
  routine_count: number;
  full_ai_count: number;
  fast_count: number;
  error_count: number;
  last_processed: string | null;
}

export interface SessionContext {
  last_machine_id: string | null;
  last_intent: string | null;
}

export interface ChatResponse {
  title: string;
  intent: string;
  query: string;
  routing_confidence: number;
  reports: ReportData[];
  machines: MachineInfo[] | null;
  report_count: number;
  copilot_answer?: string | null;
  cache_hit?: boolean;
  plant_overview?: PlantOverview | null;
}

export interface ChatMessage {
  message_id: string;
  role: "user" | "assistant";
  query: string;
  response_json: ChatResponse | null;
  created_at: string;
}

export type IngestionStatus = "pending" | "running" | "processing" | "done" | "failed";

export interface FileSummary {
  file_id?: string;
  name: string;
  type: string;
  rows: number;
  status: string;
  errors: string[];
}

export interface IngestedFile {
  file_id: string;
  ingestion_event_id: string | null;
  original_filename: string;
  file_type: string;
  status: string;
  row_count: number | null;
  page_count: number | null;
  machine_count: number | null;
  error_count: number | null;
  storage_path: string | null;
  uploaded_at: string | null;
  errors: string[];
}

export interface IngestedFileListResponse {
  total: number;
  skip: number;
  limit: number;
  files: IngestedFile[];
}

export interface IngestionEvent {
  event_id: string;
  triggered_at: string;
  triggered_by: string;
  status: IngestionStatus;
  machines_found: number;
  completed_at: string | null;
  files_uploaded: FileSummary[];
  batch_count?: number;
  batches?: ReportBatchSummary[];
}

export interface ReportBatchSummary {
  batch_id: string;
  machine_id: string;
  machine_name?: string | null;
  generated_at: string | null;
  risk_level: string | null;
  priority: string | null;
  status: string;
  root_cause?: string | null;
  failure_mode?: string | null;
  confidence?: number | null;
  recommended_action?: string | null;
  rul_hours?: number | null;
  verification_passed?: boolean | null;
  pipeline_errors?: number;
  deep_analysis_status?: "done" | "queued";
  risk_score?: number | null;
}

export interface ReportBatch {
  batch_id: string;
  event_id: string;
  machine_id: string;
  generated_at: string | null;
  root_cause: string | null;
  failure_mode: string | null;
  confidence: number | null;
  risk_level: string | null;
  recommended_action: string | null;
  priority: string | null;
  rul_hours: number | null;
  verification_passed: boolean | null;
  pipeline_errors: number;
  report: ReportData;
}

export interface StoredRoleReport {
  role: "engineer" | "supervisor" | "manager";
  content: string;
}

export interface DeepAnalysisJob {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  current_stage: string | null;
  progress_percent: number;
  machine_id: string;
  batch_id: string | null;
  error_message: string | null;
  queued_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface IngestResponse {
  event_id: string;
  status: IngestionStatus;
  machines_found: number;
  message: string;
  files: FileSummary[];
  errors: string[] | null;
}

export interface IngestStatusSummary {
  event_id: string | null;
  status: IngestionStatus;
  machines_queued: number;
  reports_generated: number;
  errors: number;
}

export interface EventListResponse {
  total: number;
  skip: number;
  limit: number;
  items: IngestionEvent[];
}

export type Role = "engineer" | "supervisor" | "manager";
