import { useEffect, useState } from "react";
import type { ChatResponse, MachineInfo, PlantOverview, ReportData } from "../../types";
import { COLORS, PRIORITY_COLORS } from "../../theme";

type Role = "engineer" | "supervisor" | "manager";

interface Props {
  result: ChatResponse | null;
  loading: boolean;
  plantOverview: PlantOverview | null;
}

const ROLE_LABELS: Record<Role, string> = {
  engineer: "Engineer",
  supervisor: "Supervisor",
  manager: "Manager",
};

// ── styles ────────────────────────────────────────────────────────────────────

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    height: "100%",
    overflow: "hidden",
  },
  header: {
    padding: "14px 16px",
    borderBottom: `1px solid ${COLORS.border}`,
    flexShrink: 0,
  },
  headerTitle: {
    fontSize: "13px",
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: "2px",
  },
  headerSub: {
    fontSize: "11px",
    color: COLORS.textMuted,
  },
  body: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "16px",
    minHeight: 0,
  },

  // ── plant overview card ────────────────────────────────────────────────────
  overviewCard: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    padding: "20px",
    marginBottom: "16px",
  },
  overviewTitle: {
    fontSize: "14px",
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: "16px",
  },
  overviewGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "10px",
    marginBottom: "12px",
  },
  overviewCell: {
    background: COLORS.cardBg,
    borderRadius: "8px",
    padding: "12px 14px",
  },
  overviewLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    marginBottom: "4px",
  },
  overviewValue: (color?: string): React.CSSProperties => ({
    fontSize: "24px",
    fontWeight: 700,
    color: color ?? COLORS.text,
    lineHeight: 1.1,
  }),
  overviewSubValue: {
    fontSize: "11px",
    color: COLORS.textMuted,
    marginTop: "2px",
  },
  overviewRow: {
    display: "flex",
    gap: "8px",
    flexWrap: "wrap" as const,
    marginTop: "8px",
  },
  overviewBadge: (bg: string, text: string): React.CSSProperties => ({
    fontSize: "11px",
    fontWeight: 600,
    padding: "3px 10px",
    borderRadius: "10px",
    background: bg,
    color: text,
  }),
  lastProcessed: {
    fontSize: "11px",
    color: COLORS.textMuted,
    marginTop: "10px",
  },

  // ── machine list (for multi-machine results) ──────────────────────────────
  machineList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
    marginBottom: "16px",
  },
  machineBtn: (active: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px",
    background: active ? COLORS.accentLight : COLORS.inputBg,
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    borderRadius: "8px",
    cursor: "pointer",
    textAlign: "left" as const,
    width: "100%",
    gap: "10px",
  }),
  machineName: {
    fontWeight: 600,
    fontSize: "13px",
    color: COLORS.text,
    flex: 1,
  },
  priorityBadge: (priority: string): React.CSSProperties => {
    const colors = PRIORITY_COLORS[priority] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      fontSize: "10px",
      fontWeight: 700,
      padding: "2px 8px",
      borderRadius: "10px",
      background: colors.bg,
      color: colors.text,
      textTransform: "capitalize" as const,
      flexShrink: 0,
    };
  },
  machineMeta: {
    fontSize: "11px",
    color: COLORS.textMuted,
  },

  // ── machine info list (for emergency / low confidence results) ────────────
  infoList: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
    marginBottom: "16px",
  },
  infoRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "10px 12px",
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "8px",
    gap: "10px",
  },
  infoName: {
    fontWeight: 600,
    fontSize: "13px",
    color: COLORS.text,
    flex: 1,
  },
  infoMeta: {
    fontSize: "11px",
    color: COLORS.textMuted,
    textAlign: "right" as const,
  },

  // ── single machine report card ────────────────────────────────────────────
  reportCard: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    padding: "16px",
    marginBottom: "14px",
  },
  reportHeader: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    marginBottom: "14px",
  },
  reportMachineName: {
    fontSize: "15px",
    fontWeight: 700,
    color: COLORS.text,
    flex: 1,
  },
  metricGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr 1fr 1fr",
    gap: "8px",
    marginBottom: "14px",
  },
  metricCell: {
    background: COLORS.cardBg,
    borderRadius: "8px",
    padding: "10px 10px",
  },
  metricLabel: {
    fontSize: "9px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    marginBottom: "4px",
  },
  metricValue: (highlight?: boolean): React.CSSProperties => ({
    fontSize: "16px",
    fontWeight: 700,
    color: highlight ? "#dc2626" : COLORS.text,
  }),

  // ── copilot answer bubble ─────────────────────────────────────────────────
  answerBubble: {
    background: COLORS.accentLight,
    border: `1px solid ${COLORS.accent}`,
    borderRadius: "10px",
    padding: "12px 14px",
    marginBottom: "14px",
  },
  answerLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.accent,
    marginBottom: "5px",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  answerText: {
    fontSize: "13px",
    color: COLORS.text,
    lineHeight: 1.6,
  },

  // ── role tabs ─────────────────────────────────────────────────────────────
  tabBar: {
    display: "flex",
    gap: "6px",
    marginBottom: "12px",
  },
  tab: (active: boolean): React.CSSProperties => ({
    padding: "5px 14px",
    borderRadius: "20px",
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    background: active ? COLORS.accentLight : "transparent",
    color: active ? COLORS.accent : COLORS.textSecondary,
    fontSize: "12px",
    fontWeight: 600,
    cursor: "pointer",
  }),
  roleContent: {
    background: COLORS.cardBg,
    borderRadius: "8px",
    padding: "14px",
    fontSize: "13px",
    color: COLORS.textSecondary,
    lineHeight: 1.7,
    whiteSpace: "pre-wrap" as const,
    maxHeight: "240px",
    overflowY: "auto" as const,
  },

  // ── fast-only note ────────────────────────────────────────────────────────
  fastNote: {
    background: "#fef3c7",
    border: "1px solid #fcd34d",
    borderRadius: "8px",
    padding: "10px 12px",
    fontSize: "12px",
    color: "#92400e",
    marginBottom: "12px",
  },

  // ── section title ─────────────────────────────────────────────────────────
  sectionTitle: {
    fontSize: "12px",
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: "10px",
  },

  // ── states ────────────────────────────────────────────────────────────────
  loadingState: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    gap: "10px",
    color: COLORS.textMuted,
  },
  loadingDot: {
    fontSize: "28px",
    animation: "spin 1s linear infinite",
  },
};

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, suffix = ""): string {
  if (v == null) return "—";
  return `${typeof v === "number" ? v.toFixed(v < 10 ? 1 : 0) : v}${suffix}`;
}

function formatLastProcessed(iso: string | null): string {
  if (!iso) return "Never";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── sub-components ────────────────────────────────────────────────────────────

function PlantOverviewCard({ ov }: { ov: PlantOverview }) {
  return (
    <div style={S.overviewCard}>
      <div style={S.overviewTitle}>Plant Overview</div>
      <div style={S.overviewGrid}>
        <div style={S.overviewCell}>
          <div style={S.overviewLabel}>Total Machines</div>
          <div style={S.overviewValue()}>{ov.total_machines}</div>
        </div>
        <div style={S.overviewCell}>
          <div style={S.overviewLabel}>Emergency</div>
          <div style={S.overviewValue(ov.emergency_count > 0 ? "#dc2626" : undefined)}>
            {ov.emergency_count}
          </div>
          <div style={S.overviewSubValue}>{ov.urgent_count} Urgent · {ov.routine_count} Routine</div>
        </div>
        <div style={S.overviewCell}>
          <div style={S.overviewLabel}>Full AI</div>
          <div style={S.overviewValue()}>{ov.full_ai_count}</div>
          <div style={S.overviewSubValue}>{ov.fast_count} Fast</div>
        </div>
        <div style={S.overviewCell}>
          <div style={S.overviewLabel}>Errors</div>
          <div style={S.overviewValue(ov.error_count > 0 ? "#dc2626" : "#16a34a")}>
            {ov.error_count}
          </div>
        </div>
      </div>
      <div style={S.lastProcessed}>Last processed: {formatLastProcessed(ov.last_processed)}</div>
    </div>
  );
}

function MachineReportCard({
  report,
  copilotAnswer,
}: {
  report: ReportData;
  copilotAnswer?: string | null;
}) {
  const [activeRole, setActiveRole] = useState<Role>("engineer");
  const isFastOnly = (report as { deep_analysis_status?: string }).deep_analysis_status === "queued";
  const confidence = report.diagnosis_confidence;

  const roleText =
    report[`${activeRole}_report` as keyof ReportData] as string | null;

  return (
    <>
      {copilotAnswer && (
        <div style={S.answerBubble}>
          <div style={S.answerLabel}>Copilot</div>
          <div style={S.answerText}>{copilotAnswer}</div>
        </div>
      )}

      <div style={S.reportCard}>
        <div style={S.reportHeader}>
          <div style={S.reportMachineName}>{report.machine?.machine_name ?? "Machine"}</div>
          {report.priority && (
            <span style={S.priorityBadge(report.priority)}>{report.priority}</span>
          )}
          {isFastOnly && (
            <span
              style={{
                fontSize: "10px",
                fontWeight: 700,
                padding: "2px 8px",
                borderRadius: "10px",
                background: "#f3f4f6",
                color: "#6b7280",
              }}
            >
              Fast Analysis
            </span>
          )}
        </div>

        <div style={S.metricGrid}>
          <div style={S.metricCell}>
            <div style={S.metricLabel}>Risk</div>
            <div style={S.metricValue(report.risk_level?.toLowerCase() === "critical")}>
              {report.risk_level ?? "—"}
            </div>
          </div>
          <div style={S.metricCell}>
            <div style={S.metricLabel}>RUL</div>
            <div style={S.metricValue()}>{fmt(report.rul_hours, "h")}</div>
          </div>
          <div style={S.metricCell}>
            <div style={S.metricLabel}>Confidence</div>
            <div style={S.metricValue((confidence ?? 1) < 0.7)}>
              {confidence != null ? confidence.toFixed(2) : "—"}
            </div>
          </div>
          <div style={S.metricCell}>
            <div style={S.metricLabel}>Anomaly</div>
            <div style={S.metricValue(report.anomaly?.detected)}>
              {report.anomaly?.detected ? "Yes" : report.anomaly ? "No" : "—"}
            </div>
          </div>
        </div>

        {report.root_cause && (
          <div style={{ marginBottom: "12px" }}>
            <div style={{ fontSize: "11px", fontWeight: 700, color: COLORS.textMuted, marginBottom: "4px" }}>
              ROOT CAUSE
            </div>
            <div style={{ fontSize: "13px", color: COLORS.text, lineHeight: 1.5 }}>
              {report.root_cause}
            </div>
          </div>
        )}
      </div>

      {!isFastOnly && (report.engineer_report || report.supervisor_report || report.manager_report) && (
        <>
          {isFastOnly && (
            <div style={S.fastNote}>
              Fast Analysis — full AI role reports not available. Run deep analysis from the Reports tab.
            </div>
          )}
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
          <div style={S.roleContent}>
            {roleText || "No report available for this role."}
          </div>
        </>
      )}
    </>
  );
}

function MachineInfoRow({ machine }: { machine: MachineInfo & { confidence?: number; rul_hours?: number; risk_score?: number } }) {
  return (
    <div style={S.infoRow}>
      <div style={S.infoName}>{machine.machine_name}</div>
      <div style={S.infoMeta}>
        <div>{machine.machine_type}</div>
        {machine.confidence != null && (
          <div>Confidence: {machine.confidence.toFixed(2)}</div>
        )}
        {machine.rul_hours != null && (
          <div>RUL: {machine.rul_hours.toFixed(0)}h</div>
        )}
        {machine.risk_score != null && (
          <div>Risk: {(machine.risk_score as number).toFixed(0)}</div>
        )}
      </div>
    </div>
  );
}

// ── main panel ────────────────────────────────────────────────────────────────

export default function CopilotPanel({ result, loading, plantOverview }: Props) {
  const [selectedIdx, setSelectedIdx] = useState(0);

  // Reset selection when result changes
  useEffect(() => {
    setSelectedIdx(0);
  }, [result]);

  const headerSub = result
    ? result.cache_hit === false
      ? "LLM summarizing…"
      : `Intent: ${result.intent} · ${result.cache_hit ? "cache hit" : ""}`
    : "Awaiting your first query";

  return (
    <div style={S.wrap}>
      <div style={S.header}>
        <div style={S.headerTitle}>
          {result ? result.title : "Plant Status"}
        </div>
        <div style={S.headerSub}>{headerSub}</div>
      </div>

      <div style={S.body}>
        {/* LOADING state */}
        {loading && (
          <div style={S.loadingState}>
            <div>Reading report cache…</div>
          </div>
        )}

        {/* RESULT: plant summary */}
        {!loading && result?.plant_overview && (
          <PlantOverviewCard ov={result.plant_overview} />
        )}

        {/* RESULT: single or multi machine reports */}
        {!loading && result && result.reports.length > 0 && (
          <>
            {result.reports.length > 1 && (
              <div style={{ marginBottom: "12px" }}>
                <div style={S.sectionTitle}>Machines ({result.reports.length})</div>
                <div style={S.machineList}>
                  {result.reports.map((r, i) => (
                    <button
                      key={r.machine?.machine_id ?? i}
                      style={S.machineBtn(selectedIdx === i)}
                      onClick={() => setSelectedIdx(i)}
                    >
                      <span style={S.machineName}>{r.machine?.machine_name ?? `Machine ${i + 1}`}</span>
                      {r.priority && <span style={S.priorityBadge(r.priority)}>{r.priority}</span>}
                      {r.rul_hours != null && (
                        <span style={S.machineMeta}>RUL {r.rul_hours.toFixed(0)}h</span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <MachineReportCard
              report={result.reports[selectedIdx] ?? result.reports[0]}
              copilotAnswer={result.reports.length === 1 ? result.copilot_answer : null}
            />
          </>
        )}

        {/* RESULT: machine info list (emergency / critical / low-confidence) */}
        {!loading && result && !result.plant_overview && result.reports.length === 0 && result.machines && result.machines.length > 0 && (
          <>
            {result.copilot_answer && (
              <div style={S.answerBubble}>
                <div style={S.answerLabel}>Copilot</div>
                <div style={S.answerText}>{result.copilot_answer}</div>
              </div>
            )}
            <div style={S.sectionTitle}>{result.title}</div>
            <div style={S.infoList}>
              {result.machines.map((m) => (
                <MachineInfoRow key={m.machine_id} machine={m as MachineInfo & { confidence?: number; rul_hours?: number }} />
              ))}
            </div>
          </>
        )}

        {/* RESULT: copilot answer only (no machines) */}
        {!loading && result && !result.plant_overview && result.reports.length === 0 && (!result.machines || result.machines.length === 0) && result.copilot_answer && (
          <div style={S.answerBubble}>
            <div style={S.answerLabel}>Copilot</div>
            <div style={S.answerText}>{result.copilot_answer}</div>
          </div>
        )}

        {/* DEFAULT: no result yet → plant overview or welcome */}
        {!loading && !result && (
          <>
            {plantOverview ? (
              <PlantOverviewCard ov={plantOverview} />
            ) : (
              <div style={{ color: COLORS.textMuted, fontSize: "13px", padding: "8px 0" }}>
                Loading plant overview…
              </div>
            )}
          </>
        )}

        {/* EMPTY RESULT: nothing matched */}
        {!loading && result && !result.plant_overview && result.reports.length === 0 && (!result.machines || result.machines.length === 0) && !result.copilot_answer && (
          <div style={{ color: COLORS.textMuted, fontSize: "13px", padding: "8px 0" }}>
            No data found for this query. Try a different question or run ingestion first.
          </div>
        )}
      </div>
    </div>
  );
}
