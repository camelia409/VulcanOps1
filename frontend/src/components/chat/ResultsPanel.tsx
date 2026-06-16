import { useState } from "react";
import type { ChatResponse, ReportData } from "../../types";
import { COLORS, PRIORITY_COLORS } from "../../theme";

type Role = "engineer" | "supervisor" | "manager";

interface Props {
  result: ChatResponse | null;
  loading: boolean;
}

const ROLE_LABELS: Record<Role, string> = {
  engineer: "Engineer",
  supervisor: "Supervisor",
  manager: "Manager",
};

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    height: "100%",
    overflow: "hidden",
  },
  header: {
    padding: "16px",
    borderBottom: `1px solid ${COLORS.border}`,
    fontSize: "15px",
    fontWeight: 600,
    color: COLORS.text,
  },
  body: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "16px",
  },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "12px",
    marginBottom: "16px",
  },
  metricCard: {
    background: "#f5f4f0",
    borderRadius: "10px",
    padding: "14px",
  },
  metricLabel: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textMuted,
    letterSpacing: "0.06em",
    textTransform: "uppercase" as const,
    marginBottom: "6px",
  },
  metricValue: {
    fontSize: "22px",
    fontWeight: 700,
    color: COLORS.text,
  },
  tabBar: {
    display: "flex",
    gap: "8px",
    marginBottom: "16px",
  },
  tab: (active: boolean): React.CSSProperties => ({
    padding: "6px 14px",
    borderRadius: "20px",
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    background: active ? COLORS.accentLight : "transparent",
    color: active ? COLORS.accentText : COLORS.textSecondary,
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
  }),
  contentBox: {
    background: "#f5f4f0",
    borderRadius: "10px",
    padding: "16px",
    fontSize: "14px",
    color: COLORS.textSecondary,
    lineHeight: 1.7,
    whiteSpace: "pre-wrap" as const,
  },
  lowConfidence: {
    marginTop: "12px",
    padding: "12px",
    background: COLORS.pendingBg,
    borderRadius: "8px",
    color: COLORS.pending,
    fontSize: "13px",
    fontWeight: 500,
  },
  emptyState: {
    flex: 1,
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    padding: "40px 24px",
    gap: "12px",
    color: COLORS.textMuted,
  },
  machineList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "8px",
    marginBottom: "16px",
  },
  machineItem: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px",
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "8px",
  },
  priorityBadge: (priority: string): React.CSSProperties => {
    const colors = PRIORITY_COLORS[priority] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      fontSize: "11px",
      fontWeight: 700,
      padding: "3px 10px",
      borderRadius: "12px",
      background: colors.bg,
      color: colors.text,
      textTransform: "capitalize" as const,
    };
  },
};

export default function ResultsPanel({ result, loading }: Props) {
  const [selectedReport, setSelectedReport] = useState(0);
  const [activeRole, setActiveRole] = useState<Role>("engineer");

  const report: ReportData | null = result?.reports[selectedReport] ?? null;

  if (loading) {
    return (
      <div style={S.wrap}>
        <div style={S.header}>Results</div>
        <div style={S.emptyState}>
          <div style={{ fontSize: "28px" }}>◉</div>
          <div style={{ fontWeight: 600 }}>Running 9-agent pipeline…</div>
          <div>Anomaly detection → Diagnosis → Strategy</div>
        </div>
      </div>
    );
  }

  if (!result) {
    return (
      <div style={S.wrap}>
        <div style={S.header}>Results</div>
        <div style={S.emptyState}>
          <div style={{ fontSize: "28px" }}>◈</div>
          <div style={{ fontWeight: 600 }}>Results</div>
          <div>Ask a question to see machine-specific outputs.</div>
        </div>
      </div>
    );
  }

  const confidence = report?.diagnosis_confidence ?? null;
  const lowConfidence = confidence !== null && confidence < 0.7;

  const roleText = report
    ? report[`${activeRole}_report`] ?? report.recommended_action ?? "No report generated."
    : "";

  return (
    <div style={S.wrap}>
      <div style={S.header}>Results</div>

      <div style={S.body}>
        {result.reports.length > 1 && (
          <div style={S.machineList}>
            {result.reports.map((r, i) => (
              <button
                key={r.machine.machine_id}
                style={{
                  ...S.machineItem,
                  borderColor:
                    selectedReport === i ? COLORS.accent : COLORS.border,
                  background: selectedReport === i ? COLORS.accentLight : COLORS.inputBg,
                }}
                onClick={() => {
                  setSelectedReport(i);
                  setActiveRole("engineer");
                }}
              >
                <span style={{ fontWeight: 600, color: COLORS.text }}>
                  {r.machine.machine_name}
                </span>
                <span style={S.priorityBadge(r.priority ?? "")}>
                  {r.priority ?? "—"}
                </span>
              </button>
            ))}
          </div>
        )}

        {report && (
          <>
            <div style={S.header}>
              {report.machine.machine_name}
              {report.risk_level && ` · ${report.risk_level}`}
            </div>

            <div style={S.metricGrid}>
              <div style={S.metricCard}>
                <div style={S.metricLabel}>RUL</div>
                <div style={S.metricValue}>
                  {report.rul_hours != null ? `${report.rul_hours}h` : "—"}
                </div>
              </div>
              <div style={S.metricCard}>
                <div style={S.metricLabel}>Confidence</div>
                <div style={S.metricValue}>
                  {confidence != null ? confidence.toFixed(2) : "—"}
                </div>
              </div>
            </div>

            <div style={S.tabBar}>
              {(["engineer", "supervisor", "manager"] as Role[]).map((role) => (
                <button
                  key={role}
                  style={S.tab(activeRole === role)}
                  onClick={() => setActiveRole(role)}
                >
                  {ROLE_LABELS[role]}
                </button>
              ))}
            </div>

            <div style={S.contentBox}>
              {roleText || "No details available for this role."}
            </div>

            {lowConfidence && (
              <div style={S.lowConfidence}>
                Manual inspection required. Diagnosis confidence is below the
                evidence threshold, so repair instructions are suppressed pending
                verification.
              </div>
            )}
          </>
        )}

        {result.machines && result.machines.length > 0 && !report && (
          <>
            <div style={{ ...S.header, marginBottom: "12px" }}>
              Critical Equipment ({result.machines.length})
            </div>
            {result.machines.map((m) => (
              <div key={m.machine_id} style={S.machineItem}>
                <span style={{ fontWeight: 600 }}>{m.machine_name}</span>
                <span style={{ color: COLORS.textMuted, fontSize: "12px" }}>
                  {m.machine_type} · {m.plant}
                </span>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
