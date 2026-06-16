import type { ReportData } from "../../types";

interface Props {
  report: ReportData;
}

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
  lineList: { display: "flex", flexDirection: "column" as const, gap: "4px" },
  lineItem: {
    fontSize: "12px",
    color: "#a3a3a3",
    padding: "6px 10px",
    background: "#161616",
    borderLeft: "2px solid #262626",
  },
  emptyState: { fontSize: "13px", color: "#404040", fontStyle: "italic" },
};

export default function SupervisorReport({ report }: Props) {
  const cost = report.estimated_cost_usd != null
    ? `$${report.estimated_cost_usd.toLocaleString("en-US", { maximumFractionDigits: 0 })}`
    : "—";

  return (
    <div style={S.root}>
      <div style={S.grid}>
        <div style={S.cell}>
          <div style={S.cellLabel}>Estimated Downtime</div>
          <div style={S.cellValue}>
            {report.estimated_downtime_hours != null
              ? `${report.estimated_downtime_hours}h`
              : "—"}
          </div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Cost Exposure</div>
          <div style={S.cellValue}>{cost}</div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>RUL Remaining</div>
          <div style={S.cellValue}>
            {report.rul_hours != null ? `${report.rul_hours}h` : "—"}
          </div>
        </div>
        <div style={S.cell}>
          <div style={S.cellLabel}>Verification</div>
          <div style={S.cellValue}>
            {report.verification
              ? report.verification.verified
                ? "Confirmed"
                : "Unconfirmed"
              : "—"}
          </div>
        </div>
      </div>

      {report.machine && (
        <div style={S.section}>
          <div style={S.sectionLabel}>Affected Production Lines</div>
          <div style={S.lineList}>
            <div style={S.lineItem}>
              {report.machine.plant} — {report.machine.location}
            </div>
          </div>
        </div>
      )}

      <div style={S.section}>
        <div style={S.sectionLabel}>Coordination & Resources</div>
        {report.supervisor_report ? (
          <p style={S.prose}>{report.supervisor_report}</p>
        ) : (
          <p style={S.emptyState}>No supervisor report generated.</p>
        )}
      </div>
    </div>
  );
}
