import { useRef, useState } from "react";
import type { IngestResponse } from "../../types";
import { ingestFiles } from "../../api/ingestApi";

interface Props {
  onIngestStarted?: (response: IngestResponse) => void;
}

const S = {
  wrap: { display: "flex", flexDirection: "column" as const, gap: "12px" },
  dropZone: (dragging: boolean): React.CSSProperties => ({
    border: `2px dashed ${dragging ? "#f97316" : "#333"}`,
    borderRadius: "8px",
    padding: "32px 24px",
    textAlign: "center" as const,
    background: dragging ? "#1a0f00" : "#111",
    color: "#737373",
    cursor: "pointer",
    transition: "all 0.15s ease",
  }),
  dropTitle: {
    fontSize: "14px",
    color: "#e5e5e5",
    fontWeight: 500,
    marginBottom: "6px",
  },
  dropDesc: {
    fontSize: "12px",
    color: "#525252",
  },
  fileList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
  },
  fileItem: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "8px 12px",
    background: "#0f0f0f",
    border: "1px solid #1f1f1f",
    fontSize: "12px",
    color: "#a3a3a3",
  },
  removeBtn: {
    background: "transparent",
    border: "none",
    color: "#737373",
    cursor: "pointer",
    fontSize: "14px",
  },
  actions: { display: "flex", gap: "8px" },
  primaryBtn: (disabled: boolean): React.CSSProperties => ({
    flex: 1,
    background: disabled ? "#1a1a1a" : "#f97316",
    color: disabled ? "#404040" : "#0a0a0a",
    border: "none",
    padding: "10px 16px",
    fontSize: "12px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    cursor: disabled ? "not-allowed" : "pointer",
  }),
  secondaryBtn: {
    background: "transparent",
    border: "1px solid #333",
    color: "#737373",
    padding: "10px 16px",
    fontSize: "12px",
    cursor: "pointer",
  },
  errorBox: {
    background: "#1a0000",
    border: "1px solid #3f0000",
    color: "#dc2626",
    fontSize: "12px",
    padding: "8px 12px",
  },
  successBox: {
    background: "#0d1a0d",
    border: "1px solid #1a3a1a",
    color: "#16a34a",
    fontSize: "12px",
    padding: "8px 12px",
  },
};

export default function FileDropZone({ onIngestStarted }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<IngestResponse | null>(null);

  const handleFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const accepted = Array.from(incoming).filter(
      (f) =>
        f.name.toLowerCase().endsWith(".csv") ||
        f.name.toLowerCase().endsWith(".pdf")
    );
    if (accepted.length !== incoming.length) {
      setError("Only .csv and .pdf files are accepted.");
    }
    setFiles((prev) => [...prev, ...accepted]);
  };

  const removeFile = (idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  };

  const clear = () => {
    setFiles([]);
    setError(null);
    setLastResult(null);
  };

  const upload = async () => {
    if (files.length === 0 || uploading) return;
    setUploading(true);
    setError(null);
    setLastResult(null);

    try {
      const response = await ingestFiles(files);
      setLastResult(response);
      onIngestStarted?.(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={S.wrap}>
      <div
        style={S.dropZone(dragging)}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
      >
        <div style={S.dropTitle}>Drop CSV/PDF files here</div>
        <div style={S.dropDesc}>or click to browse</div>
      </div>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".csv,.pdf"
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
      />

      {files.length > 0 && (
        <div style={S.fileList}>
          {files.map((file, idx) => (
            <div key={`${file.name}-${idx}`} style={S.fileItem}>
              <span>{file.name}</span>
              <button style={S.removeBtn} onClick={() => removeFile(idx)}>
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {error && <div style={S.errorBox}>{error}</div>}
      {lastResult && !error && (
        <div style={S.successBox}>
          <div>{lastResult.message}</div>
          {(lastResult.files ?? []).length > 0 && (
            <div style={{ marginTop: "8px" }}>
              {(lastResult.files ?? []).map((f) => (
                <div key={f.file_id ?? f.name} style={{ fontSize: "11px", marginTop: "2px" }}>
                  {f.name}: <span style={{ color: f.status === "success" ? "#16a34a" : "#f59e0b" }}>{f.status}</span>
                  {f.errors && f.errors.length > 0 && (
                    <span style={{ color: "#f59e0b" }}> — {f.errors.join("; ")}</span>
                  )}
                </div>
              ))}
            </div>
          )}
          {lastResult.errors && lastResult.errors.length > 0 && (
            <div style={{ color: "#f59e0b", marginTop: "6px" }}>
              Warnings: {lastResult.errors.join("; ")}
            </div>
          )}
        </div>
      )}

      <div style={S.actions}>
        <button
          style={S.primaryBtn(files.length === 0 || uploading)}
          disabled={files.length === 0 || uploading}
          onClick={upload}
        >
          {uploading ? "Ingesting…" : "Ingest Files"}
        </button>
        {files.length > 0 && (
          <button style={S.secondaryBtn} onClick={clear}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
