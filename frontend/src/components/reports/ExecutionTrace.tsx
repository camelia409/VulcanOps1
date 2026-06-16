export interface TraceItem {
  agent_name: string;
  start_time: string;
  end_time: string;
  latency_ms: number;
  status: string;
  skip_reason?: string;
}

interface Props {
  trace: TraceItem[];
}

const AGENTS: { key: string; label: string; llm?: boolean }[] = [
  { key: "anomaly_agent",               label: "Anomaly Detection" },
  { key: "prognostics_agent",           label: "Prognostics" },
  { key: "evidence_retrieval_agent",    label: "Evidence Retrieval" },
  { key: "diagnosis_agent",             label: "Diagnosis", llm: true },
  { key: "evidence_verification_agent", label: "Evidence Verification" },
  { key: "operational_impact_agent",    label: "Operational Impact" },
  { key: "maintenance_strategy_agent",  label: "Maintenance Strategy" },
  { key: "plant_priority_agent",        label: "Plant Priority" },
  { key: "communication_agent",         label: "Communication", llm: true },
  { key: "finalize",                    label: "Report Generation" },
];

const STATUS_COLOR: Record<string, string> = {
  success: "#16a34a",
  error:   "#dc2626",
  skipped: "#f59e0b",
  partial: "#f97316",
};

const STATUS_LABEL: Record<string, string> = {
  success: "OK",
  error:   "ERR",
  skipped: "SKIP",
  partial: "PARTIAL",
};

const S = {
  wrap: { marginTop: "1px" },
  header: {
    fontSize: "10px",
    letterSpacing: "0.14em",
    color: "#525252",
    textTransform: "uppercase" as const,
    fontWeight: 600,
    padding: "12px 16px 8px",
    borderBottom: "1px solid #1f1f1f",
  },
  row: (isLast: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "9px 16px",
    borderBottom: isLast ? "none" : "1px solid #1a1a1a",
    background: "#111",
  }),
  indicator: (color: string): React.CSSProperties => ({
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: color,
    flexShrink: 0,
  }),
  label: {
    flex: 1,
    fontSize: "13px",
    color: "#d4d4d4",
  },
  llmTag: {
    fontSize: "9px",
    letterSpacing: "0.1em",
    color: "#f97316",
    border: "1px solid #7c3a0a",
    padding: "1px 5px",
    textTransform: "uppercase" as const,
    fontWeight: 600,
  },
  latency: {
    fontSize: "11px",
    color: "#525252",
    fontVariantNumeric: "tabular-nums" as const,
    minWidth: "58px",
    textAlign: "right" as const,
  },
  statusBadge: (color: string): React.CSSProperties => ({
    fontSize: "9px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    color,
    minWidth: "42px",
    textAlign: "right" as const,
  }),
  emptyRow: {
    padding: "9px 16px",
    borderBottom: "1px solid #1a1a1a",
    display: "flex",
    alignItems: "center",
    gap: "10px",
    background: "#111",
  },
  emptyDot: {
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: "#262626",
    flexShrink: 0,
  },
  emptyLabel: { fontSize: "13px", color: "#404040" },
};

export default function ExecutionTrace({ trace }: Props) {
  const traceMap = new Map(trace.map((t) => [t.agent_name, t]));

  return (
    <div style={S.wrap}>
      <div style={S.header}>Agent Execution Trace</div>
      {AGENTS.map((agent, idx) => {
        const item = traceMap.get(agent.key);
        const isLast = idx === AGENTS.length - 1;

        if (!item) {
          return (
            <div key={agent.key} style={S.emptyRow}>
              <div style={S.emptyDot} />
              <span style={S.emptyLabel}>{agent.label}</span>
              {agent.llm && <span style={S.llmTag}>LLM</span>}
            </div>
          );
        }

        const color = STATUS_COLOR[item.status] ?? "#525252";
        const statusLabel = STATUS_LABEL[item.status] ?? item.status.toUpperCase();
        const latencyStr =
          item.latency_ms < 1000
            ? `${Math.round(item.latency_ms)}ms`
            : `${(item.latency_ms / 1000).toFixed(1)}s`;

        return (
          <div key={agent.key} style={S.row(isLast)}>
            <div style={S.indicator(color)} />
            <span style={S.label}>{agent.label}</span>
            {agent.llm && <span style={S.llmTag}>LLM</span>}
            <span style={S.latency}>{latencyStr}</span>
            <span style={S.statusBadge(color)}>{statusLabel}</span>
          </div>
        );
      })}
    </div>
  );
}
