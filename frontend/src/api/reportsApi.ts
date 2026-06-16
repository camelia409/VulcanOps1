import { API_BASE_URL } from "../config";
import type {
  EventListResponse,
  IngestionEvent,
  ReportBatch,
  StoredRoleReport,
} from "../types";

export async function listEvents(
  skip = 0,
  limit = 20
): Promise<EventListResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports?skip=${skip}&limit=${limit}`);
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as EventListResponse;
}

export async function listTodaysEvents(): Promise<{
  date: string;
  items: IngestionEvent[];
}> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/today`);
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as { date: string; items: IngestionEvent[] };
}

export async function getEvent(eventId: string): Promise<IngestionEvent> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/event/${eventId}`);
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as IngestionEvent;
}

export async function deleteEvent(eventId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/event/${eventId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
}

export async function getBatch(batchId: string): Promise<ReportBatch> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/batch/${batchId}`);
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as ReportBatch;
}

export async function getRoleReport(
  batchId: string,
  role: "engineer" | "supervisor" | "manager"
): Promise<StoredRoleReport> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/batch/${batchId}/${role}`);
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as StoredRoleReport;
}

export function getRolePdfUrl(
  batchId: string,
  role: "engineer" | "supervisor" | "manager"
): string {
  return `${API_BASE_URL}/api/v1/reports/batch/${batchId}/pdf?role=${role}`;
}

export interface DeepAnalysisResult {
  batch_id: string;
  machine_id: string;
  deep_analysis_status: "done";
  message: string;
}

export async function runDeepAnalysis(machineId: string): Promise<DeepAnalysisResult> {
  const res = await fetch(`${API_BASE_URL}/api/v1/reports/deep-analyze/${machineId}`, {
    method: "POST",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as DeepAnalysisResult;
}
