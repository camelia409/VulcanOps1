import { useEffect, useRef, useState } from "react";
import type { EvidenceChainItem, ProcurementGapInfo, ReportBatch, ReportData, Role, StoredRoleReport } from "../../types";
import { getBatch, getDeepAnalysisJob, getRoleReport, getRolePdfUrl, runDeepAnalysis } from "../../api/reportsApi";
import { COLORS, PRIORITY_COLORS } from "../../theme";

interface Props {
  batchId: string | null;
  onDeepAnalysisComplete?: (newBatchId: string) => void;
}

// ReportData is extended at runtime with fields added by the 4-layer pipeline.
type ExtendedReport = ReportData & {
  deep_analysis_status?: "done" | "queued";
  risk_score?: number;
  evidence_chain?: EvidenceChainItem[];
  explainability_score?: number | null;
  procurement_gap?: ProcurementGapInfo | null;
};

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
  centred: {
    flex: 1,
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    gap: "8px",
    color: COLORS.textMuted,
    fontSize: "14px",
    padding: "24px",
    textAlign: "center" as const,
  },
  header: {
    display: "flex",
    alignItems: "flex-start",
    justifyContent: "space-between",
    padding: "16px",
    borderBottom: `1px solid ${COLORS.border}`,
    flexShrink: 0,
    gap: "12px",
  },
  headerLeft: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "4px",
    minWidth: 0,
    flex: 1,
  },
  machineName: {
    fontSize: "15px",
    fontWeight: 700,
    color: COLORS.text,
  },
  machineType: {
    fontSize: "12px",
    color: COLORS.textMuted,
  },
  headerBadge: (isDeep: boolean): React.CSSProperties => ({
    fontSize: "11px",
    fontWeight: 700,
    padding: "3px 9px",
    borderRadius: "10px",
    background: isDeep ? "#eef2ff" : "#f3f4f6",
    color: isDeep ? "#4338ca" : "#6b7280",
    flexShrink: 0,
  }),
  exportBtn: {
    background: "transparent",
    border: `1px solid ${COLORS.borderStrong}`,
    borderRadius: "8px",
    padding: "7px 13px",
    fontSize: "12px",
    fontWeight: 600,
    color: COLORS.textSecondary,
    cursor: "pointer",
    flexShrink: 0,
  },
  tabBar: {
    display: "flex",
    gap: "6px",
    padding: "10px 16px",
    borderBottom: `1px solid ${COLORS.border}`,
    flexShrink: 0,
  },
  tab: (active: boolean): React.CSSProperties => ({
    padding: "5px 13px",
    borderRadius: "18px",
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    background: active ? COLORS.accentLight : "transparent",
    color: active ? COLORS.accentText : COLORS.textSecondary,
    fontSize: "13px",
    fontWeight: 600,
    cursor: "pointer",
  }),
  body: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "16px",
  },

  // ── meta strip (Full AI) ──
  metaStrip: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: "8px",
    marginBottom: "16px",
  },
  metaBox: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "8px",
    padding: "8px 10px",
  },
  metaLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "3px",
  },
  metaValue: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.text,
  },

  // ── role report content ──
  contentBox: {
    background: "#f5f4f0",
    borderRadius: "10px",
    padding: "16px",
    fontSize: "14px",
    color: COLORS.textSecondary,
    lineHeight: 1.75,
    whiteSpace: "pre-wrap" as const,
  },
  roleError: {
    padding: "12px 14px",
    color: COLORS.failed,
    fontSize: "13px",
    background: COLORS.failedBg,
    borderRadius: "8px",
    marginBottom: "12px",
  },

  // ── explainability card ──
  explainCard: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "10px",
    marginBottom: "14px",
    overflow: "hidden" as const,
  },
  explainHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 14px",
    cursor: "pointer",
    background: "#f9fafb",
  },
  explainTitle: {
    fontSize: "13px",
    fontWeight: 700,
    color: COLORS.text,
  },
  explainScore: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textMuted,
    background: "#eef2ff",
    padding: "2px 8px",
    borderRadius: "10px",
  },
  explainBody: {
    padding: "12px 14px",
    borderTop: `1px solid ${COLORS.border}`,
  },
  explainStep: {
    display: "flex",
    gap: "10px",
    marginBottom: "10px",
    fontSize: "12px",
    color: COLORS.textSecondary,
    lineHeight: 1.5,
  },
  explainStepNum: {
    flexShrink: 0,
    width: "20px",
    height: "20px",
    borderRadius: "50%",
    background: COLORS.accent,
    color: "white",
    fontSize: "11px",
    fontWeight: 700,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  explainStepMeta: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "2px",
  },
  evidenceSection: {
    background: "#f9fafb",
    border: `1px solid ${COLORS.border}`,
    borderRadius: "8px",
    padding: "12px 14px",
    marginBottom: "12px",
  },
  evidenceSectionTitle: {
    fontSize: "12px",
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: "8px",
  },
  evidenceRow: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "12px",
    color: COLORS.textSecondary,
    marginBottom: "4px",
  },
  evidenceCheck: {
    color: "#16a34a",
    fontWeight: 700,
    fontSize: "13px",
  },
  evidenceMissing: {
    color: "#d1d5db",
    fontWeight: 700,
    fontSize: "13px",
  },
  procurementAlert: {
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: "8px",
    padding: "10px 12px",
    fontSize: "12px",
    color: "#991b1b",
    lineHeight: 1.5,
    marginTop: "8px",
  },

  // ── fast-only panel ──
  fastPanel: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "16px",
  },
  fastHeader: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    marginBottom: "4px",
  },
  fastTitle: {
    fontSize: "15px",
    fontWeight: 700,
    color: COLORS.text,
  },
  fastSubtitle: {
    fontSize: "13px",
    color: COLORS.textMuted,
    lineHeight: 1.5,
  },
  statsGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(2, 1fr)",
    gap: "8px",
  },
  statBox: (color: string): React.CSSProperties => ({
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "8px",
    padding: "10px 12px",
    borderLeft: `3px solid ${color}`,
  }),
  statLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "3px",
  },
  statValue: {
    fontSize: "14px",
    fontWeight: 700,
    color: COLORS.text,
  },

  // ── availability checklist ──
  capabilityCard: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "10px",
    padding: "14px 16px",
  },
  capTitle: {
    fontSize: "12px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    marginBottom: "10px",
  },
  capGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "6px 16px",
  },
  capRow: (available: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: "6px",
    fontSize: "12px",
    color: available ? "#15803d" : COLORS.textLight,
  }),
  capIcon: (available: boolean): React.CSSProperties => ({
    fontSize: "12px",
    color: available ? "#16a34a" : "#d1d5db",
    fontWeight: 700,
    flexShrink: 0,
  }),

  // ── deep analysis button ──
  deepCard: {
    background: "#fafaf9",
    border: `1px solid ${COLORS.border}`,
    borderRadius: "10px",
    padding: "16px",
  },
  deepTitle: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.text,
    marginBottom: "6px",
  },
  deepBody: {
    fontSize: "12px",
    color: COLORS.textMuted,
    lineHeight: 1.5,
    marginBottom: "12px",
  },
  deepBtn: (loading: boolean): React.CSSProperties => ({
    background: loading ? COLORS.inputBg : COLORS.accent,
    color: loading ? COLORS.textMuted : "white",
    border: "none",
    borderRadius: "8px",
    padding: "9px 16px",
    fontSize: "13px",
    fontWeight: 700,
    cursor: loading ? "not-allowed" : "pointer",
    display: "flex",
    alignItems: "center",
    gap: "6px",
  }),
  deepError: {
    marginTop: "10px",
    padding: "10px 12px",
    background: COLORS.failedBg,
    border: `1px solid ${COLORS.failed}`,
    borderRadius: "8px",
    fontSize: "12px",
    color: COLORS.failed,
  },
  anomalyBox: {
    background: "#fff7ed",
    border: "1px solid #fed7aa",
    borderRadius: "8px",
    padding: "12px 14px",
    fontSize: "13px",
    color: "#9a3412",
    lineHeight: 1.5,
  },
};

// ── helpers ───────────────────────────────────────────────────────────────────

function isFastOnly(batch: ReportBatch | null): boolean {
  if (!batch) return false;
  return (batch.report as ExtendedReport)?.deep_analysis_status === "queued";
}

function riskColor(level: string | null | undefined): string {
  switch (level?.toLowerCase()) {
    case "critical": return "#dc2626";
    case "high":     return "#ea580c";
    case "medium":   return "#d97706";
    default:         return "#6b7280";
  }
}

function priorityColor(p: string | null | undefined): string {
  const c = PRIORITY_COLORS[p?.toLowerCase() ?? ""];
  return c?.text ?? COLORS.textMuted;
}

function hasEvidenceType(chain: EvidenceChainItem[] | undefined, type: string): boolean {
  return chain?.some((item) => item.type === type) ?? false;
}

function evidenceTypeLabel(type: string): string {
  switch (type) {
    case "sensor": return "Sensor data";
    case "history": return "Maintenance history";
    case "manual": return "Equipment manual";
    default: return type;
  }
}

// ── component ─────────────────────────────────────────────────────────────────

export default function ReportViewer({ batchId, onDeepAnalysisComplete }: Props) {
  const [batch, setBatch] = useState<ReportBatch | null>(null);
  const [activeRole, setActiveRole] = useState<Role>("engineer");
  const [roleReport, setRoleReport] = useState<StoredRoleReport | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [roleLoading, setRoleLoading] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);
  const [deepAnalyzing, setDeepAnalyzing] = useState(false);
  const [deepError, setDeepError] = useState<string | null>(null);
  const [pollingJobId, setPollingJobId] = useState<string | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [explainExpanded, setExplainExpanded] = useState(false);

  // ── fetch batch on batchId change ──
  useEffect(() => {
    setBatch(null);
    setRoleReport(null);
    setBatchError(null);
    setRoleError(null);
    setDeepError(null);
    setDeepAnalyzing(false);
    setPollingJobId(null);
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }

    if (!batchId) return;
    let cancelled = false;
    setBatchLoading(true);

    getBatch(batchId)
      .then((b) => { if (!cancelled) setBatch(b); })
      .catch((err) => { if (!cancelled) setBatchError(err instanceof Error ? err.message : "Failed to load report"); })
      .finally(() => { if (!cancelled) setBatchLoading(false); });

    return () => { cancelled = true; };
  }, [batchId]);

  // ── poll deep-analysis job until done/failed ──
  useEffect(() => {
    if (!pollingJobId) return;

    setDeepAnalyzing(true);
    setDeepError(null);

    const finish = () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      setPollingJobId(null);
      setDeepAnalyzing(false);
    };

    const poll = async () => {
      try {
        const job = await getDeepAnalysisJob(pollingJobId);
        if (job.status === "done" && job.batch_id) {
          finish();
          onDeepAnalysisComplete?.(job.batch_id);
          const refreshed = await getBatch(job.batch_id);
          setBatch(refreshed);
        } else if (job.status === "failed") {
          finish();
          setDeepError(job.error_message || "Deep analysis failed");
        }
        // queued/running: keep polling
      } catch (err) {
        finish();
        setDeepError(err instanceof Error ? err.message : "Polling failed");
      }
    };

    poll();
    pollIntervalRef.current = setInterval(poll, 2000);

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [pollingJobId]);

  // ── fetch role report (full-AI only, re-runs on role change) ──
  useEffect(() => {
    setRoleReport(null);
    setRoleError(null);
    if (!batchId || !batch || isFastOnly(batch)) return;

    let cancelled = false;
    setRoleLoading(true);

    getRoleReport(batchId, activeRole)
      .then((r) => { if (!cancelled) setRoleReport(r); })
      .catch((err) => {
        if (!cancelled) setRoleError(err instanceof Error ? err.message : "No report found");
      })
      .finally(() => { if (!cancelled) setRoleLoading(false); });

    return () => { cancelled = true; };
  }, [batchId, batch, activeRole]);

  // ── deep analysis trigger ──
  const handleRunDeepAnalysis = async () => {
    if (!batch || deepAnalyzing || pollingJobId) return;
    setDeepError(null);
    setDeepAnalyzing(true);
    try {
      const result = await runDeepAnalysis(batch.machine_id);
      setPollingJobId(result.job_id);
    } catch (err) {
      setDeepError(err instanceof Error ? err.message : "Deep analysis failed");
      setDeepAnalyzing(false);
    }
  };

  // ── render: empty ──
  if (!batchId) {
    return (
      <div style={S.wrap}>
        <div style={S.centred}>
          <div style={{ fontSize: "24px" }}>📋</div>
          <div style={{ fontWeight: 600, color: COLORS.textSecondary }}>No machine selected</div>
          <div style={{ fontSize: "12px" }}>Click a machine in the list to view its report.</div>
        </div>
      </div>
    );
  }

  // ── render: loading batch ──
  if (batchLoading) {
    return (
      <div style={S.wrap}>
        <div style={S.centred}>
          <div style={{ fontSize: "13px" }}>Loading report…</div>
        </div>
      </div>
    );
  }

  // ── render: batch fetch error ──
  if (batchError) {
    return (
      <div style={S.wrap}>
        <div style={S.centred}>
          <div style={{ fontSize: "20px" }}>⚠️</div>
          <div style={{ fontWeight: 600, color: COLORS.failed }}>Failed to load report</div>
          <div style={{ fontSize: "12px", color: COLORS.textMuted }}>{batchError}</div>
        </div>
      </div>
    );
  }

  const fastOnly = isFastOnly(batch);
  const report = batch?.report as ExtendedReport | undefined;
  const machine = report?.machine;

  return (
    <div style={S.wrap}>
      {/* ── header ── */}
      <div style={S.header}>
        <div style={S.headerLeft}>
          <div style={S.machineName}>
            {fastOnly ? "⚡ " : "⭐ "}
            {machine?.machine_name ?? "Machine"}
          </div>
          <div style={S.machineType}>
            {machine?.machine_type ?? ""}{machine?.plant ? ` · ${machine.plant}` : ""}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0 }}>
          <span style={S.headerBadge(!fastOnly)}>
            {fastOnly ? "Fast analysis" : "Full AI"}
          </span>
          {!fastOnly && (
            <a
              href={getRolePdfUrl(batchId, activeRole)}
              target="_blank"
              rel="noreferrer"
              style={{ textDecoration: "none" }}
            >
              <button style={S.exportBtn}>↓ PDF</button>
            </a>
          )}
        </div>
      </div>

      {/* ── role tabs (full-AI only) ── */}
      {!fastOnly && (
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
      )}

      <div style={S.body}>
        {fastOnly ? (
          // ── FAST-ONLY panel ──
          <div style={S.fastPanel}>
            {/* subtitle */}
            <div>
              <div style={S.fastHeader}>
                <div style={S.fastTitle}>Fast Analysis Results</div>
              </div>
              <div style={S.fastSubtitle}>
                This machine was not selected for full AI investigation.
                5 fast agents ran successfully. Deep analysis is reserved
                for the top-3 highest-risk machines each run.
              </div>
            </div>

            {/* stats grid */}
            <div style={S.statsGrid}>
              <div style={S.statBox(riskColor(report?.risk_level))}>
                <div style={S.statLabel}>Risk level</div>
                <div style={{ ...S.statValue, color: riskColor(report?.risk_level) }}>
                  {report?.risk_level ?? "—"}
                </div>
              </div>
              <div style={S.statBox("#6366f1")}>
                <div style={S.statLabel}>Remaining useful life</div>
                <div style={S.statValue}>
                  {report?.rul_hours != null ? `${report.rul_hours.toFixed(0)} h` : "—"}
                </div>
              </div>
              <div style={S.statBox(priorityColor(report?.priority))}>
                <div style={S.statLabel}>Priority</div>
                <div style={{ ...S.statValue, color: priorityColor(report?.priority) }}>
                  {report?.priority ?? "—"}
                </div>
              </div>
              <div style={S.statBox(report?.anomaly?.detected ? "#ea580c" : "#6b7280")}>
                <div style={S.statLabel}>Anomaly</div>
                <div style={S.statValue}>
                  {report?.anomaly?.detected
                    ? (report.anomaly.sensor ?? "Detected")
                    : "None"}
                </div>
              </div>
            </div>

            {/* anomaly detail */}
            {report?.anomaly?.detected && (
              <div style={S.anomalyBox}>
                <strong>Anomaly detail:</strong>{" "}
                {report.anomaly.sensor} — value {report.anomaly.value?.toFixed(2)},
                threshold {report.anomaly.threshold?.toFixed(2)},
                deviation {report.anomaly.deviation_percent?.toFixed(1)}%
              </div>
            )}

            {/* ✓/✗ capability checklist */}
            <div style={S.capabilityCard}>
              <div style={S.capTitle}>Analysis availability</div>
              <div style={S.capGrid}>
                {[
                  { label: "Risk Score",         ok: true  },
                  { label: "Failure Mode",        ok: false },
                  { label: "Priority",            ok: true  },
                  { label: "Root Cause",          ok: false },
                  { label: "RUL Estimate",        ok: true  },
                  { label: "Diagnosis",           ok: false },
                  { label: "Operational Impact",  ok: true  },
                  { label: "Engineer Report",     ok: false },
                  { label: "Anomaly Detection",   ok: true  },
                  { label: "Supervisor Report",   ok: false },
                  { label: "Evidence Retrieval",  ok: true  },
                  { label: "Manager Report",      ok: false },
                ].map(({ label, ok }) => (
                  <div key={label} style={S.capRow(ok)}>
                    <span style={S.capIcon(ok)}>{ok ? "✓" : "✗"}</span>
                    <span>{label}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* [Run Deep Analysis] */}
            <div style={S.deepCard}>
              <div style={S.deepTitle}>Run full AI analysis on this machine</div>
              <div style={S.deepBody}>
                Runs all 9 agents including LLM diagnosis, evidence verification,
                and role report generation for engineer, supervisor, and manager.
                Takes approximately 20–40 seconds.
              </div>
              <button
                style={S.deepBtn(deepAnalyzing)}
                onClick={handleRunDeepAnalysis}
                disabled={deepAnalyzing}
              >
                {deepAnalyzing ? (
                  <>
                    <span style={{ fontSize: "14px" }}>⏳</span>
                    Running deep analysis…
                  </>
                ) : (
                  <>
                    <span style={{ fontSize: "14px" }}>⭐</span>
                    Run Deep Analysis
                  </>
                )}
              </button>
              {deepError && (
                <div style={S.deepError}>
                  <strong>Error:</strong> {deepError}
                </div>
              )}
            </div>
          </div>
        ) : (
          // ── FULL-AI panel ──
          <>
            {/* meta strip */}
            <div style={S.metaStrip}>
              <div style={S.metaBox}>
                <div style={S.metaLabel}>Risk level</div>
                <div style={{ ...S.metaValue, color: riskColor(report?.risk_level) }}>
                  {report?.risk_level ?? "—"}
                </div>
              </div>
              <div style={S.metaBox}>
                <div style={S.metaLabel}>RUL</div>
                <div style={S.metaValue}>
                  {report?.rul_hours != null ? `${report.rul_hours.toFixed(0)} h` : "—"}
                </div>
              </div>
              <div style={S.metaBox}>
                <div style={S.metaLabel}>Priority</div>
                <div style={{ ...S.metaValue, color: priorityColor(report?.priority) }}>
                  {report?.priority ?? "—"}
                </div>
              </div>
              <div style={S.metaBox}>
                <div style={S.metaLabel}>Confidence</div>
                <div style={S.metaValue}>
                  {report?.diagnosis_confidence != null
                    ? `${Math.round((report.diagnosis_confidence as number) * 100)}%`
                    : batch?.confidence != null
                    ? `${Math.round(batch.confidence * 100)}%`
                    : "—"}
                </div>
              </div>
            </div>

            {/* diagnosis row */}
            {(report?.root_cause || batch?.root_cause) && (
              <div style={{ ...S.metaBox, marginBottom: "14px" }}>
                <div style={S.metaLabel}>Diagnosis</div>
                <div style={{ fontSize: "13px", color: COLORS.textSecondary, lineHeight: 1.5, marginTop: "2px" }}>
                  {report?.root_cause ?? batch?.root_cause}
                </div>
              </div>
            )}

            {/* explainability card */}
            {report?.evidence_chain && report.evidence_chain.length > 0 && (
              <div style={S.explainCard}>
                <div
                  style={S.explainHeader}
                  onClick={() => setExplainExpanded((v) => !v)}
                  role="button"
                  aria-expanded={explainExpanded}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span style={S.explainTitle}>Explainability</span>
                    {report.explainability_score != null && (
                      <span style={S.explainScore}>{report.explainability_score}/100</span>
                    )}
                  </div>
                  <span style={{ fontSize: "12px", color: COLORS.textMuted }}>
                    {explainExpanded ? "▲" : "▼"} View evidence chain
                  </span>
                </div>
                {explainExpanded && (
                  <div style={S.explainBody}>
                    {report.evidence_chain.map((item) => (
                      <div key={item.step} style={S.explainStep}>
                        <div style={S.explainStepNum}>{item.step}</div>
                        <div>
                          <div style={S.explainStepMeta}>{evidenceTypeLabel(item.type)} · {item.source}</div>
                          <div>{item.evidence}</div>
                        </div>
                      </div>
                    ))}
                    {report.procurement_gap?.procurement_gap && (
                      <div style={S.procurementAlert}>
                        <strong>Procurement gap detected:</strong>{" "}
                        {report.procurement_gap.recommended_action}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* role error */}
            {roleError && (
              <div style={S.roleError}>
                No {activeRole} report found for this machine.
              </div>
            )}

            {/* loading role report */}
            {roleLoading && !roleReport && (
              <div style={{ color: COLORS.textMuted, fontSize: "13px", marginBottom: "12px" }}>
                Loading {activeRole} report…
              </div>
            )}

            {/* role-specific evidence / assurance header */}
            {roleReport && report?.evidence_chain && report.evidence_chain.length > 0 && (
              <div style={S.evidenceSection}>
                {activeRole === "engineer" && (
                  <>
                    <div style={S.evidenceSectionTitle}>Evidence Sources</div>
                    {["sensor", "history", "manual"].map((type) => {
                      const ok = hasEvidenceType(report.evidence_chain, type);
                      return (
                        <div key={type} style={S.evidenceRow}>
                          <span style={ok ? S.evidenceCheck : S.evidenceMissing}>{ok ? "✓" : "✗"}</span>
                          <span>{evidenceTypeLabel(type)}</span>
                        </div>
                      );
                    })}
                  </>
                )}
                {activeRole === "supervisor" && (
                  <>
                    <div style={S.evidenceSectionTitle}>Decision Confidence</div>
                    <div style={S.evidenceRow}>
                      <span style={S.evidenceCheck}>✓</span>
                      <span>Explainability Score: {report.explainability_score ?? "—"}/100</span>
                    </div>
                    <div style={S.evidenceRow}>
                      <span style={S.evidenceCheck}>✓</span>
                      <span>Evidence Sources: {report.evidence_chain.length}</span>
                    </div>
                  </>
                )}
                {activeRole === "manager" && (
                  <>
                    <div style={S.evidenceSectionTitle}>Business Assurance</div>
                    <div style={S.evidenceRow}>
                      <span style={S.evidenceCheck}>✓</span>
                      <span>Decision traceability: {report.explainability_score != null && report.explainability_score >= 70 ? "High" : "Moderate"}</span>
                    </div>
                    <div style={S.evidenceRow}>
                      <span style={report.procurement_gap?.procurement_gap ? S.evidenceCheck : S.evidenceMissing}>
                        {report.procurement_gap?.procurement_gap ? "✓" : "✗"}
                      </span>
                      <span>Procurement Gap: {report.procurement_gap?.procurement_gap ? "Detected" : "None"}</span>
                    </div>
                    {report.procurement_gap?.procurement_gap && (
                      <div style={S.procurementAlert}>
                        {report.procurement_gap.recommended_action}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* role report content */}
            {roleReport && (
              <div style={S.contentBox}>
                {roleReport.content || `No ${activeRole} report generated for this machine.`}
              </div>
            )}

            {/* no report yet (shouldn't normally show, but Phase 8 fallback) */}
            {!roleLoading && !roleReport && !roleError && (
              <div style={{ color: COLORS.textMuted, fontSize: "13px" }}>
                No {activeRole} report available.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
