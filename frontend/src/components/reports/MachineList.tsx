import type { IngestionEvent, ReportBatchSummary } from "../../types";
import { COLORS, PRIORITY_COLORS } from "../../theme";

interface Props {
  event: IngestionEvent | null;
  selectedBatchId?: string;
  onSelectBatch: (batch: ReportBatchSummary) => void;
}

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "5px",
  },
  sectionLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textLight,
    textTransform: "uppercase" as const,
    letterSpacing: "0.07em",
    padding: "6px 0 2px 2px",
  },
  row: (active: boolean): React.CSSProperties => ({
    display: "flex",
    flexDirection: "column" as const,
    gap: "5px",
    padding: "9px 11px",
    background: active ? COLORS.accentLight : COLORS.inputBg,
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    borderRadius: "8px",
    cursor: "pointer",
    transition: "border-color 0.1s",
  }),
  rowTop: {
    display: "flex",
    alignItems: "center",
    gap: "7px",
  },
  typeIcon: (isDeep: boolean): React.CSSProperties => ({
    fontSize: "13px",
    lineHeight: 1,
    flexShrink: 0,
    color: isDeep ? "#4338ca" : "#6b7280",
  }),
  name: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.text,
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  priorityBadge: (priority: string): React.CSSProperties => {
    const colors = PRIORITY_COLORS[priority] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      fontSize: "10px",
      fontWeight: 700,
      padding: "2px 7px",
      borderRadius: "10px",
      background: colors.bg,
      color: colors.text,
      textTransform: "capitalize" as const,
      flexShrink: 0,
    };
  },
  rowBottom: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    paddingLeft: "20px",
  },
  typeBadge: (isDeep: boolean): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 600,
    padding: "1px 7px",
    borderRadius: "8px",
    background: isDeep ? "#eef2ff" : "#f3f4f6",
    color: isDeep ? "#4338ca" : "#6b7280",
    flexShrink: 0,
  }),
  meta: {
    fontSize: "10px",
    color: COLORS.textLight,
  },
  riskScore: {
    fontSize: "10px",
    color: COLORS.textLight,
    marginLeft: "auto",
  },
  empty: {
    fontSize: "13px",
    color: COLORS.textMuted,
    padding: "12px 2px",
  },
};

// Sort: Full AI first → risk score desc within each group → name asc as tiebreak
function sortBatches(batches: ReportBatchSummary[]): ReportBatchSummary[] {
  return [...batches].sort((a, b) => {
    const aDeep = a.deep_analysis_status !== "queued" ? 0 : 1;
    const bDeep = b.deep_analysis_status !== "queued" ? 0 : 1;
    if (aDeep !== bDeep) return aDeep - bDeep;
    const rDiff = (b.risk_score ?? 0) - (a.risk_score ?? 0);
    if (Math.abs(rDiff) > 0.01) return rDiff;
    return (a.machine_name ?? "").localeCompare(b.machine_name ?? "");
  });
}

export default function MachineList({ event, selectedBatchId, onSelectBatch }: Props) {
  if (!event) {
    return <div style={S.empty}>Select a report run to view machines.</div>;
  }

  const batches = sortBatches(event.batches ?? []);
  const deepBatches = batches.filter((b) => b.deep_analysis_status !== "queued");
  const fastBatches = batches.filter((b) => b.deep_analysis_status === "queued");

  if (batches.length === 0) {
    return <div style={S.empty}>No machines in this run.</div>;
  }

  const renderRow = (batch: ReportBatchSummary) => {
    const isDeep = batch.deep_analysis_status !== "queued";
    return (
      <div
        key={batch.batch_id}
        style={S.row(batch.batch_id === selectedBatchId)}
        onClick={() => onSelectBatch(batch)}
      >
        <div style={S.rowTop}>
          <span style={S.typeIcon(isDeep)}>{isDeep ? "⭐" : "⚡"}</span>
          <span style={S.name}>{batch.machine_name ?? batch.machine_id?.slice(0, 8) ?? "Machine"}</span>
          {batch.priority && (
            <span style={S.priorityBadge(batch.priority)}>{batch.priority}</span>
          )}
        </div>
        <div style={S.rowBottom}>
          <span style={S.typeBadge(isDeep)}>
            {isDeep ? "Full AI" : "Fast analysis"}
          </span>
          {batch.rul_hours != null && (
            <span style={S.meta}>RUL {batch.rul_hours.toFixed(0)}h</span>
          )}
          {batch.risk_score != null && (
            <span style={S.riskScore}>Risk {batch.risk_score.toFixed(0)}</span>
          )}
        </div>
      </div>
    );
  };

  return (
    <div style={S.wrap}>
      {deepBatches.length > 0 && (
        <>
          <div style={S.sectionLabel}>⭐ Full AI analysis ({deepBatches.length})</div>
          {deepBatches.map(renderRow)}
        </>
      )}
      {fastBatches.length > 0 && (
        <>
          <div style={S.sectionLabel}>⚡ Fast analysis ({fastBatches.length})</div>
          {fastBatches.map(renderRow)}
        </>
      )}
    </div>
  );
}
