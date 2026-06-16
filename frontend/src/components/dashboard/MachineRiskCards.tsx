import type { ReportData } from "../../types";

interface Props {
  reports: ReportData[];
  selectedIndex: number;
  onSelect: (index: number) => void;
}

const RISK_COLOR: Record<string, string> = {
  critical: "#dc2626",
  high:     "#f97316",
  medium:   "#f59e0b",
  low:      "#16a34a",
};

const CRIT_COLOR: Record<string, string> = {
  critical: "#dc2626",
  high:     "#f97316",
  medium:   "#f59e0b",
  low:      "#16a34a",
};

const S = {
  wrap: {
    display: "flex",
    gap: "1px",
    background: "#1f1f1f",
    border: "1px solid #1f1f1f",
  },
  card: (active: boolean, hasError: boolean): React.CSSProperties => ({
    flex: 1,
    background: active ? "#1a1a1a" : "#111",
    padding: "12px 14px",
    cursor: "pointer",
    borderBottom: active ? "2px solid #f97316" : "2px solid transparent",
    opacity: hasError ? 0.6 : 1,
    minWidth: 0,
  }),
  machineName: {
    fontSize: "13px",
    fontWeight: 600,
    color: "#e5e5e5",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    marginBottom: "4px",
  },
  machineType: {
    fontSize: "11px",
    color: "#737373",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
    marginBottom: "10px",
  },
  row: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  risk: (color: string): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.1em",
    textTransform: "uppercase" as const,
    color,
  }),
  rul: {
    fontSize: "11px",
    color: "#525252",
    fontVariantNumeric: "tabular-nums" as const,
  },
  critBadge: (color: string): React.CSSProperties => ({
    display: "inline-block",
    width: "6px",
    height: "6px",
    borderRadius: "50%",
    background: color,
    marginRight: "5px",
  }),
  errorTag: {
    fontSize: "10px",
    color: "#dc2626",
    letterSpacing: "0.08em",
  },
  emptyWrap: {
    padding: "20px 16px",
    color: "#404040",
    fontSize: "13px",
    fontStyle: "italic",
  },
};

export default function MachineRiskCards({ reports, selectedIndex, onSelect }: Props) {
  if (!reports.length) {
    return <div style={S.emptyWrap}>No machines investigated yet.</div>;
  }

  return (
    <div style={S.wrap}>
      {reports.map((r, i) => {
        const m = r.machine;
        const riskColor = RISK_COLOR[r.risk_level ?? ""] ?? "#525252";
        const critColor = CRIT_COLOR[m?.criticality ?? ""] ?? "#525252";
        const rulStr = r.rul_hours != null ? `${r.rul_hours}h RUL` : null;

        return (
          <div
            key={m?.machine_id ?? i}
            style={S.card(i === selectedIndex, r.has_errors)}
            onClick={() => onSelect(i)}
          >
            <div style={S.machineName}>{m?.machine_name ?? "Unknown"}</div>
            <div style={S.machineType}>{m?.machine_type ?? "—"}</div>

            <div style={S.row}>
              {r.has_errors && !r.risk_level ? (
                <span style={S.errorTag}>PIPELINE ERROR</span>
              ) : (
                <span style={S.risk(riskColor)}>
                  <span style={S.critBadge(critColor)} />
                  {r.risk_level ?? "—"}
                </span>
              )}
              {rulStr && <span style={S.rul}>{rulStr}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
