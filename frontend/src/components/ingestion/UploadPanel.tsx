import { useRef, useState } from "react";
import type { IngestResponse } from "../../types";
import { ingestFiles } from "../../api/ingestApi";
import { COLORS } from "../../theme";

interface Props {
  onIngestStarted?: (response: IngestResponse) => void;
}

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "16px",
    flex: 1,
    minHeight: 0,
  },
  dropZone: (dragging: boolean): React.CSSProperties => ({
    border: `1.5px dashed ${dragging ? COLORS.accent : COLORS.borderStrong}`,
    borderRadius: "10px",
    padding: "40px 24px",
    textAlign: "center" as const,
    background: dragging ? COLORS.accentLight : COLORS.inputBg,
    color: COLORS.textMuted,
    cursor: "pointer",
    transition: "all 0.15s ease",
  }),
  cloudIcon: {
    fontSize: "32px",
    color: COLORS.accent,
    marginBottom: "10px",
  },
  dropTitle: {
    fontSize: "14px",
    color: COLORS.text,
    fontWeight: 500,
    marginBottom: "4px",
  },
  dropDesc: {
    fontSize: "12px",
    color: COLORS.textMuted,
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
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "6px",
    fontSize: "12px",
    color: COLORS.textSecondary,
  },
  removeBtn: {
    background: "transparent",
    border: "none",
    color: COLORS.textMuted,
    cursor: "pointer",
    fontSize: "16px",
    lineHeight: 1,
  },
  primaryBtn: (disabled: boolean): React.CSSProperties => ({
    alignSelf: "flex-start",
    display: "flex",
    alignItems: "center",
    gap: "6px",
    background: "transparent",
    color: disabled ? COLORS.textLight : COLORS.text,
    border: `1px solid ${disabled ? COLORS.border : COLORS.borderStrong}`,
    borderRadius: "8px",
    padding: "8px 14px",
    fontSize: "13px",
    fontWeight: 600,
    cursor: disabled ? "not-allowed" : "pointer",
  }),
  errorBox: {
    background: COLORS.failedBg,
    border: `1px solid ${COLORS.failed}`,
    color: COLORS.failed,
    fontSize: "12px",
    padding: "10px 12px",
    borderRadius: "8px",
  },
  successBox: {
    background: COLORS.doneBg,
    border: `1px solid ${COLORS.done}`,
    color: COLORS.done,
    fontSize: "12px",
    padding: "10px 12px",
    borderRadius: "8px",
  },
};

export default function UploadPanel({ onIngestStarted }: Props) {
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

  const upload = async () => {
    if (files.length === 0 || uploading) return;
    setUploading(true);
    setError(null);
    setLastResult(null);

    try {
      const response = await ingestFiles(files);
      setLastResult(response);
      onIngestStarted?.(response);
      setFiles([]);
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
        <div style={S.cloudIcon}>☁</div>
        <div style={S.dropTitle}>Drop CSVs or PDFs here</div>
        <div style={S.dropDesc}>machines · sensors · maintenance · manuals · SOPs</div>
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
          {lastResult.message}
          {lastResult.errors && lastResult.errors.length > 0 && (
            <div style={{ marginTop: "6px" }}>
              Warnings: {lastResult.errors.join("; ")}
            </div>
          )}
        </div>
      )}

      <button
        style={S.primaryBtn(files.length === 0 || uploading)}
        disabled={files.length === 0 || uploading}
        onClick={upload}
      >
        <span>⚡</span>
        <span>{uploading ? "Running pipeline…" : "Ingest & run pipeline"}</span>
      </button>
    </div>
  );
}
