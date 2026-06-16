import { useEffect, useState } from "react";
import { getIngestStatus } from "../../api/ingestApi";
import { COLORS, STATUS_COLORS } from "../../theme";
import type { IngestStatusSummary } from "../../types";

const REFRESH_INTERVAL_MS = 5000;

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "12px",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  title: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.textSecondary,
  },
  statusBadge: (status: string): React.CSSProperties => {
    const colors = STATUS_COLORS[status] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      fontSize: "11px",
      fontWeight: 700,
      padding: "4px 10px",
      borderRadius: "12px",
      background: colors.bg,
      color: colors.text,
      textTransform: "capitalize" as const,
    };
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(3, 1fr)",
    gap: "16px",
  },
  card: {
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "10px",
    padding: "16px",
  },
  label: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textMuted,
    letterSpacing: "0.06em",
    textTransform: "uppercase" as const,
    marginBottom: "8px",
  },
  value: {
    fontSize: "28px",
    fontWeight: 700,
    color: COLORS.text,
    lineHeight: 1,
  },
};

export default function PipelineStatus() {
  const [status, setStatus] = useState<IngestStatusSummary>({
    event_id: null,
    status: "pending",
    machines_queued: 0,
    reports_generated: 0,
    errors: 0,
  });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const s = await getIngestStatus();
        setStatus(s);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load status");
      }
    };
    load();
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={S.wrap}>
      <div style={S.header}>
        <div style={S.title}>Pipeline status — current event</div>
        <span style={S.statusBadge(status.status)}>{status.status}</span>
      </div>

      {error && (
        <div style={{ color: COLORS.failed, fontSize: "12px" }}>{error}</div>
      )}

      <div style={S.grid}>
        <div style={S.card}>
          <div style={S.label}>Machines queued</div>
          <div style={S.value}>{status.machines_queued}</div>
        </div>
        <div style={S.card}>
          <div style={S.label}>Reports generated</div>
          <div style={S.value}>{status.reports_generated}</div>
        </div>
        <div style={S.card}>
          <div style={S.label}>Errors</div>
          <div style={S.value}>{status.errors}</div>
        </div>
      </div>
    </div>
  );
}
