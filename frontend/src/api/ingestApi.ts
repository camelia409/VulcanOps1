import { API_BASE_URL } from "../config";
import type {
  IngestResponse,
  IngestStatusSummary,
  IngestedFileListResponse,
} from "../types";

export async function ingestFiles(files: File[]): Promise<IngestResponse> {
  if (files.length === 0) {
    throw new Error("No files provided");
  }

  const form = new FormData();
  files.forEach((file) => form.append("files", file));

  const res = await fetch(`${API_BASE_URL}/api/v1/ingest`, {
    method: "POST",
    body: form,
  });

  const data = await res.json();

  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }

  return data as IngestResponse;
}

export async function getIngestStatus(): Promise<IngestStatusSummary> {
  const res = await fetch(`${API_BASE_URL}/api/v1/ingest/status`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return data as IngestStatusSummary;
}

export async function listIngestedFiles(
  skip = 0,
  limit = 100
): Promise<IngestedFileListResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/upload/files?skip=${skip}&limit=${limit}`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return data as IngestedFileListResponse;
}

export async function deleteIngestedFile(fileId: string): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/api/v1/upload/files/${fileId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const data = await res.json();
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
}
