import { useEffect, useState } from "react";
import type { ReportData } from "../../types";

// ── types from GET /api/v1/investigate/status ──────────────────────────────────

interface CheckpointInfo {
  passed: boolean;
  count?: number;
  detail: string;
}

interface StatusResponse {
  checkpoints: Record<string, CheckpointInfo>;
  llm: {
    provider: string;
    diagnosis_model: string;
    communication_model: string;
    api_key_configured: boolean;
  };
}

interface Props {
  report: ReportData | null;
}

// ── styles ─────────────────────────────────────────────────────────────────────

const S = {
  root: {
    borderTop: "1px solid #1f1f1f",
    background: "#0a0a0a",
  },
  section: {
    padding: "12px 16px 10px",
    borderBottom: "1px solid #1a1a1a",
  },
  heading: {
    fontSize: "10px",
    letterSpacing: "0.14em",
    color: "#525252",
    textTransform: "uppercase" as const,
    fontWeight: 600,
    marginBottom: "8px",
  },
  row: {
    display: "flex",
    alignItems: "flex-start",
    gap: "8px",
    marginBottom: "5px",
  },
  dot: (passed: boolean | null): React.CSSProperties => ({
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background:
      passed === null ? "#404040" : passed ? "#16a34a" : "#dc2626",
    flexShrink: 0,
    marginTop: "4px",
  }),
  label: {
    fontSize: "11px",
    color: "#737373",
    lineHeight: 1.5,
    flex: 1,
  },
  count: {
    fontSize: "11px",
    color: "#404040",
    fontVariantNumeric: "tabular-nums" as const,
    flexShrink: 0,
  },
  valueRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: "5px",
  },
  valueLabel: {
    fontSize: "11px",
    color: "#525252",
  },
  valueData: {
    fontSize: "11px",
    color: "#a3a3a3",
    fontVariantNumeric: "tabular-nums" as const,
  },
  statusBadge: (s: string): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color:
      s === "success" ? "#16a34a"
      : s === "partial" ? "#f59e0b"
      : s === "error"   ? "#dc2626"
      : "#404040",
  }),
  modelText: {
    fontSize: "11px",
    color: "#525252",
    wordBreak: "break-all" as const,
    lineHeight: 1.4,
  },
  keyStatus: (ok: boolean): React.CSSProperties => ({
    fontSize: "11px",
    color: ok ? "#16a34a" : "#dc2626",
  }),
  loadingText: {
    fontSize: "11px",
    color: "#404040",
    padding: "10px 16px",
  },
};

// ── checkpoint display names ───────────────────────────────────────────────────

const CHECKPOINT_LABELS: Record<string, string> = {
  machines_uploaded:            "Machine registry",
  sensor_data_uploaded:         "Sensor data",
  maintenance_history_uploaded: "Maintenance history",
  documents_uploaded:           "Manuals & SOPs",
};

// ── component ──────────────────────────────────────────────────────────────────

export default function SystemStatus({ report }: Props) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [fetchError, setFetchError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/v1/investigate/status");
        if (!res.ok) { setFetchError(true); return; }
        const data: StatusResponse = await res.json();
        if (!cancelled) setStatus(data);
      } catch {
        if (!cancelled) setFetchError(true);
      }
    };

    fetchStatus();
    // Refresh every 30 seconds so upload state is reflected without page reload
    const interval = setInterval(fetchStatus, 30_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // ── derive pipeline metadata from the active report ──────────────────────────
  // Defensive: guard all array accesses to prevent undefined errors
  const pipelineStatus = report
    ? (report.has_errors && (report.pipeline_errors ?? 0) > 0 ? "partial" : "success")
    : null;

  const totalMs = report
    ? (report.execution_trace ?? []).reduce((sum, t) => sum + (t.latency_ms ?? 0), 0)
    : null;

  const agentsRan = report
    ? (report.execution_trace ?? []).filter(
        (t) => t.status !== "skipped" && t.status !== "error"
      ).length
    : null;

  const agentsTotal = report ? (report.execution_trace ?? []).length : null;

  return (
    <div style={S.root}>

      {/* ── Section 1: Data availability ──────────────────────────────────── */}
      <div style={S.section}>
        <div style={S.heading}>Data Availability</div>

        {fetchError && (
          <div style={{ ...S.label, color: "#dc2626" }}>
            Could not reach /api/v1/investigate/status
          </div>
        )}

        {!status && !fetchError && (
          <div style={S.loadingText}>Checking…</div>
        )}

        {status &&
          Object.entries(CHECKPOINT_LABELS).map(([key, label]) => {
            const cp = status.checkpoints[key];
            if (!cp) return null;
            return (
              <div key={key} style={S.row}>
                <div style={S.dot(cp.passed)} />
                <span style={S.label}>{label}</span>
                {cp.count !== undefined && (
                  <span style={S.count}>{cp.count.toLocaleString()}</span>
                )}
              </div>
            );
          })}
      </div>

      {/* ── Section 2: Pipeline status (only when a report exists) ────────── */}
      {report && (
        <div style={S.section}>
          <div style={S.heading}>Last Pipeline</div>

          <div style={S.valueRow}>
            <span style={S.valueLabel}>Status</span>
            <span style={S.statusBadge(pipelineStatus ?? "")}>
              {pipelineStatus ?? "—"}
            </span>
          </div>

          {totalMs !== null && (
            <div style={S.valueRow}>
              <span style={S.valueLabel}>Total time</span>
              <span style={S.valueData}>{Math.round(totalMs).toLocaleString()} ms</span>
            </div>
          )}

          {agentsRan !== null && agentsTotal !== null && (
            <div style={S.valueRow}>
              <span style={S.valueLabel}>Agents executed</span>
              <span style={S.valueData}>{agentsRan} / {agentsTotal}</span>
            </div>
          )}

          {(report.pipeline_errors ?? 0) > 0 && (
            <div style={S.valueRow}>
              <span style={S.valueLabel}>Agent errors</span>
              <span style={{ ...S.valueData, color: "#dc2626" }}>
                {report.pipeline_errors ?? 0}
              </span>
            </div>
          )}

          {/* Post-pipeline checkpoints: diagnosis + role reports */}
          <div style={{ marginTop: "6px" }}>
            {[
              { label: "Diagnosis produced", passed: Boolean(report.root_cause) },
              { label: "Role reports generated", passed: Boolean(report.engineer_report) },
            ].map(({ label, passed }) => (
              <div key={label} style={S.row}>
                <div style={S.dot(passed)} />
                <span style={S.label}>{label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Section 3: LLM configuration ──────────────────────────────────── */}
      {status?.llm && (
        <div style={S.section}>
          <div style={S.heading}>LLM Configuration</div>

          <div style={S.valueRow}>
            <span style={S.valueLabel}>Provider</span>
            <span style={S.valueData}>{status.llm.provider}</span>
          </div>

          <div style={S.valueRow}>
            <span style={S.valueLabel}>API key</span>
            <span style={S.keyStatus(status.llm.api_key_configured)}>
              {status.llm.api_key_configured ? "Configured" : "Missing"}
            </span>
          </div>

          <div style={{ marginTop: "6px" }}>
            <div style={{ ...S.valueLabel, marginBottom: "3px" }}>Diagnosis model</div>
            <div style={S.modelText}>{status.llm.diagnosis_model}</div>
          </div>

          <div style={{ marginTop: "6px" }}>
            <div style={{ ...S.valueLabel, marginBottom: "3px" }}>Communication model</div>
            <div style={S.modelText}>{status.llm.communication_model}</div>
          </div>
        </div>
      )}
    </div>
  );
}
