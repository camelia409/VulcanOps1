import { useEffect, useState } from "react";
import type { ReportBatch, Role, StoredRoleReport } from "../../types";
import { getBatch, getRoleReport } from "../../api/reportsApi";
import PDFDownloadButton from "./PDFDownloadButton";

interface Props {
  batchId: string;
}

const S = {
  wrap: { display: "flex", flexDirection: "column" as const, height: "100%" },
  header: {
    padding: "12px 16px",
    background: "#0f0f0f",
    borderBottom: "1px solid #1f1f1f",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  title: {
    fontSize: "13px",
    fontWeight: 600,
    color: "#e5e5e5",
  },
  tabBar: {
    display: "flex",
    gap: "1px",
    background: "#1f1f1f",
    padding: "0 16px",
    borderBottom: "1px solid #1f1f1f",
  },
  tab: (active: boolean): React.CSSProperties => ({
    padding: "10px 14px 9px",
    fontSize: "11px",
    fontWeight: 600,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: active ? "#f97316" : "#525252",
    borderBottom: active ? "2px solid #f97316" : "2px solid transparent",
    cursor: "pointer",
    background: "transparent",
    border: "none",
    borderBottomWidth: "2px",
    borderBottomStyle: "solid",
    borderBottomColor: active ? "#f97316" : "transparent",
  }),
  content: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "16px",
    background: "#0a0a0a",
  },
  prose: {
    fontSize: "13px",
    color: "#a3a3a3",
    lineHeight: 1.7,
    whiteSpace: "pre-wrap" as const,
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
  meta: {
    fontSize: "11px",
    color: "#525252",
    marginTop: "8px",
  },
};

export default function RoleReportViewer({ batchId }: Props) {
  const [batch, setBatch] = useState<ReportBatch | null>(null);
  const [activeRole, setActiveRole] = useState<Role>("engineer");
  const [roleReport, setRoleReport] = useState<StoredRoleReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getBatch(batchId)
      .then((b) => {
        if (cancelled) return;
        setBatch(b);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load batch");
      })
      .finally(() => setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [batchId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getRoleReport(batchId, activeRole)
      .then((r) => {
        if (cancelled) return;
        setRoleReport(r);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load report");
      })
      .finally(() => setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [batchId, activeRole]);

  return (
    <div style={S.wrap}>
      <div style={S.header}>
        <div style={S.title}>
          {batch ? batch.report?.machine?.machine_name ?? "Report" : "Report"}
        </div>
        <PDFDownloadButton batchId={batchId} role={activeRole} />
      </div>

      <div style={S.tabBar}>
        {(["engineer", "supervisor", "manager"] as Role[]).map((role) => (
          <button
            key={role}
            style={S.tab(activeRole === role)}
            onClick={() => setActiveRole(role)}
          >
            {role}
          </button>
        ))}
      </div>

      <div style={S.content}>
        {error && <div style={S.error}>{error}</div>}
        {loading && !roleReport && <div style={S.empty}>Loading…</div>}
        {roleReport && (
          <>
            <div style={S.prose}>{roleReport.content}</div>
            {batch && (
              <div style={S.meta}>
                Root cause: {batch.root_cause ?? "—"} · Confidence:{" "}
                {batch.confidence != null
                  ? `${Math.round(batch.confidence * 100)}%`
                  : "—"}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
