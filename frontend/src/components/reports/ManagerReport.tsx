import type { ReportData } from "../../types";

interface Props {
  report: ReportData;
}

const RISK_COLOR: Record<string, string> = {
  critical: "#dc2626",
  high:     "#f97316",
  medium:   "#f59e0b",
  low:      "#16a34a",
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
  cell: { background: "#111", padding: "10px 14px" },
  cellLabel: {
    fontSize: "10px",
    color: "#525252",
    letterSpacing: "0.12em",
    textTransform: "uppercase" as const,
    marginBottom: "4px",
  },
  cellValue: { fontSize: "13px", color: "#e5e5e5", fontWeight: 500 },
  riskValue: (risk: string): React.CSSProperties => ({
    fontSize: "13px",
    fontWeight: 700,
    color: RISK_COLOR[risk] ?? "#e5e5e5",
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
  }),
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
  flagList: { display: "flex", flexDirection: "column" as const, gap: "4px" },
  flag: {
    fontSize: "12px",
    color: "#fbbf24",
    padding: "6px 10px",
    background: "#1c1500",
    borderLeft: "2px solid #f59e0b",
  },
  emptyState: { fontSize: "13px", color: "#404040", fontStyle: "italic" },
};

export default function ManagerReport({ report }: Props) {
  const cost = report.estimated_cost_usd != null
    ? `$${report.estimated_cost_usd.toLocaleString("en-US", { maximumFractionDigits: 0 })}`
    : "—";

  return (
    <div style={S.root}>
      <div style={S.grid}>
        <div style={S.cell}>
          <div style={S.cellLabel}>Risk Level</div>
          <div style={S.riskValue(report.risk_level ?? "")}>
            {report.risk_level ?? "—"}
          </div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Total Cost Exposure</div>
          <div style={S.cellValue}>{cost}</div>
        </div>
        <div style={{ ...S.cell, gridColumn: "1 / -1" }}>
          <div style={S.cellLabel}>Root Cause</div>
          <div style={S.cellValue}>{report.root_cause ?? "—"}</div>
        </div>
      </div>

      {report.verification?.verification_notes && (
        <div style={S.section}>
          <div style={S.sectionLabel}>Verification Notes</div>
          <p style={S.prose}>{report.verification.verification_notes}</p>
        </div>
      )}

      <div style={S.section}>
        <div style={S.sectionLabel}>Business Impact & Strategy</div>
        {report.manager_report ? (
          <p style={S.prose}>{report.manager_report}</p>
        ) : (
          <p style={S.emptyState}>No manager report generated.</p>
        )}
      </div>
    </div>
  );
}
