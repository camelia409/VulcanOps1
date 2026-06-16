import { useEffect, useState } from "react";
import type { IngestedFile } from "../../types";
import { deleteIngestedFile, listIngestedFiles } from "../../api/ingestApi";

const REFRESH_INTERVAL_MS = 5000;

const S = {
  wrap: { display: "flex", flexDirection: "column" as const, gap: "1px" },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 16px",
    background: "#0f0f0f",
    borderBottom: "1px solid #1f1f1f",
  },
  title: {
    fontSize: "10px",
    letterSpacing: "0.14em",
    color: "#525252",
    textTransform: "uppercase" as const,
    fontWeight: 600,
  },
  refreshBtn: {
    background: "transparent",
    border: "none",
    color: "#737373",
    fontSize: "11px",
    cursor: "pointer",
  },
  row: {
    padding: "12px 16px",
    background: "#111",
    borderBottom: "1px solid #1a1a1a",
  },
  rowTop: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "6px",
  },
  filename: {
    fontSize: "13px",
    color: "#e5e5e5",
    fontWeight: 500,
    wordBreak: "break-all" as const,
  },
  deleteBtn: {
    background: "transparent",
    border: "1px solid #3f0000",
    color: "#dc2626",
    fontSize: "11px",
    padding: "3px 10px",
    cursor: "pointer",
  },
  fileType: {
    fontSize: "10px",
    color: "#737373",
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
  },
  status: (status: string): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color:
      status === "success"
        ? "#16a34a"
        : status === "error"
        ? "#dc2626"
        : status === "pending"
        ? "#f97316"
        : "#525252",
  }),
  meta: {
    fontSize: "12px",
    color: "#a3a3a3",
  },
  errors: {
    fontSize: "11px",
    color: "#dc2626",
    marginTop: "6px",
  },
  empty: {
    padding: "24px 16px",
    textAlign: "center" as const,
    color: "#404040",
    fontSize: "13px",
  },
  error: {
    padding: "12px 16px",
    color: "#dc2626",
    fontSize: "12px",
    background: "#1a0000",
  },
  loading: {
    padding: "12px 16px",
    color: "#525252",
    fontSize: "12px",
  },
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString();
}

function formatCounts(file: IngestedFile): string {
  const parts: string[] = [];
  if (file.row_count != null) parts.push(`${file.row_count} rows`);
  if (file.page_count != null) parts.push(`${file.page_count} pages`);
  if (file.machine_count != null) parts.push(`${file.machine_count} machines`);
  if (file.error_count != null && file.error_count > 0) {
    parts.push(`${file.error_count} errors`);
  }
  return parts.join(" · ") || "processed";
}

export default function IngestionHistory() {
  const [files, setFiles] = useState<IngestedFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listIngestedFiles(0, 100);
      setFiles(data?.files ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load history");
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (fileId: string) => {
    try {
      await deleteIngestedFile(fileId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete file");
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={S.header}>
        <div style={S.title}>Ingested Files</div>
        <button style={S.refreshBtn} onClick={load} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <div style={S.error}>{error}</div>}
      {loading && files.length === 0 && (
        <div style={S.loading}>Loading ingestion history…</div>
      )}

      <div style={{ ...S.wrap, flex: 1, overflowY: "auto", minHeight: 0 }}>
        {files.length === 0 && !error && !loading ? (
          <div style={S.empty}>No ingested files yet.</div>
        ) : (
          files.map((file) => (
            <div key={file.file_id} style={S.row}>
              <div style={S.rowTop}>
                <span style={S.filename}>{file.original_filename}</span>
                <button
                  style={S.deleteBtn}
                  onClick={() => handleDelete(file.file_id)}
                  disabled={loading}
                >
                  Delete
                </button>
              </div>
              <div style={S.meta}>
                <span style={S.fileType}>{file.file_type}</span> ·{" "}
                <span style={S.status(file.status)}>{file.status}</span> ·{" "}
                {formatCounts(file)} · {formatDate(file.uploaded_at)}
              </div>
              {(file.errors ?? []).length > 0 && (
                <div style={S.errors}>
                  {(file.errors ?? []).join("; ")}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
