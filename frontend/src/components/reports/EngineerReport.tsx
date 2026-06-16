import type { ReportData } from "../../types";

interface Props {
  report: ReportData;
}

const PRIORITY_COLOR: Record<string, string> = {
  emergency: "#dc2626",
  urgent:    "#f97316",
  scheduled: "#f59e0b",
  routine:   "#16a34a",
};

const S = {
  root: { padding: "16px" },
  grid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "1px",
    background: "#1f1f1f",
    border: "1px solid #1f1f1f",
    marginBottom: "16px",
  },
  cell: {
    background: "#111",
    padding: "10px 14px",
  },
  cellLabel: {
    fontSize: "10px",
    color: "#525252",
    letterSpacing: "0.12em",
    textTransform: "uppercase" as const,
    marginBottom: "4px",
  },
  cellValue: {
    fontSize: "13px",
    color: "#e5e5e5",
    fontWeight: 500,
  },
  section: { marginBottom: "16px" },
  sectionLabel: {
    fontSize: "10px",
    color: "#525252",
    letterSpacing: "0.12em",
    textTransform: "uppercase" as const,
    marginBottom: "8px",
    paddingBottom: "6px",
    borderBottom: "1px solid #1f1f1f",
  },
  prose: {
    fontSize: "13px",
    color: "#a3a3a3",
    lineHeight: 1.7,
    whiteSpace: "pre-wrap" as const,
  },
  partsList: { listStyle: "none", display: "flex", flexDirection: "column" as const, gap: "4px" },
  partsItem: {
    fontSize: "12px",
    color: "#a3a3a3",
    padding: "6px 10px",
    background: "#161616",
    borderLeft: "2px solid #f97316",
  },
  emptyState: { fontSize: "13px", color: "#404040", fontStyle: "italic" },
  priorityBadge: (priority: string): React.CSSProperties => ({
    display: "inline-block",
    fontSize: "11px",
    fontWeight: 700,
    letterSpacing: "0.1em",
    textTransform: "uppercase" as const,
    color: PRIORITY_COLOR[priority] ?? "#e5e5e5",
  }),
};

export default function EngineerReport({ report }: Props) {
  const confidence = report.diagnosis_confidence != null
    ? `${Math.round(report.diagnosis_confidence * 100)}%`
    : "—";

  return (
    <div style={S.root}>
      <div style={S.grid}>
        <div style={S.cell}>
          <div style={S.cellLabel}>Failure Mode</div>
          <div style={S.cellValue}>{report.failure_mode ?? "—"}</div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Priority</div>
          <div style={S.cellValue}>
            <span style={S.priorityBadge(report.priority ?? "")}>
              {report.priority ?? "—"}
            </span>
          </div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Est. Repair</div>
          <div style={S.cellValue}>
            {report.estimated_downtime_hours != null
              ? `${report.estimated_downtime_hours}h`
              : "—"}
          </div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Diagnosis Confidence</div>
          <div style={S.cellValue}>{confidence}</div>
        </div>
      </div>

      {report.parts_required?.length > 0 && (
        <div style={S.section}>
          <div style={S.sectionLabel}>Parts Required</div>
          <ul style={S.partsList}>
            {report.parts_required.map((part, i) => (
              <li key={i} style={S.partsItem}>{part}</li>
            ))}
          </ul>
        </div>
      )}

      <div style={S.section}>
        <div style={S.sectionLabel}>Field Instructions & Safety</div>
        {report.engineer_report ? (
          <p style={S.prose}>{report.engineer_report}</p>
        ) : (
          <p style={S.emptyState}>No engineer report generated.</p>
        )}
      </div>
    </div>
  );
}
