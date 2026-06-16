import type { ReportBatchSummary } from "../../types";

interface Props {
  batch: ReportBatchSummary;
  active: boolean;
  onClick: () => void;
}

const RISK_COLOR: Record<string, string> = {
  critical: "#dc2626",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#16a34a",
};

const S = {
  card: (active: boolean): React.CSSProperties => ({
    padding: "12px 16px",
    background: active ? "#1a1a1a" : "#111",
    borderBottom: "1px solid #1a1a1a",
    cursor: "pointer",
    borderLeft: active ? "2px solid #f97316" : "2px solid transparent",
  }),
  rowTop: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "6px",
  },
  batchId: {
    fontSize: "11px",
    color: "#737373",
    fontFamily: "monospace",
  },
  risk: (risk: string | null): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color: RISK_COLOR[risk ?? ""] ?? "#525252",
  }),
  meta: {
    fontSize: "12px",
    color: "#a3a3a3",
  },
};

export default function ReportBatchCard({ batch, active, onClick }: Props) {
  return (
    <div style={S.card(active)} onClick={onClick}>
      <div style={S.rowTop}>
        <span style={S.batchId}>{batch.batch_id.slice(0, 8)}…</span>
        <span style={S.risk(batch.risk_level)}>{batch.risk_level ?? "—"}</span>
      </div>
      <div style={S.meta}>
        Priority: {batch.priority ?? "—"} · Status: {batch.status}
      </div>
    </div>
  );
}
