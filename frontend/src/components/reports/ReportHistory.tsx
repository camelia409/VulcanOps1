import { useEffect, useState } from "react";
import type { IngestionEvent } from "../../types";
import { listEvents } from "../../api/reportsApi";
import { COLORS, STATUS_COLORS } from "../../theme";

interface Props {
  selectedEventId?: string;
  onSelectEvent: (event: IngestionEvent) => void;
}

const REFRESH_INTERVAL_MS = 5000;

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "16px",
  },
  dateGroup: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
  },
  groupTitle: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.06em",
    marginBottom: "2px",
  },
  row: (active: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "11px 12px",
    background: active ? COLORS.accentLight : COLORS.inputBg,
    border: `1px solid ${active ? COLORS.accent : COLORS.border}`,
    borderRadius: "10px",
    cursor: "pointer",
    gap: "10px",
  }),
  left: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    minWidth: 0,
    flex: 1,
  },
  badge: (s: string): React.CSSProperties => {
    const c = STATUS_COLORS[s] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      fontSize: "10px",
      fontWeight: 700,
      padding: "2px 7px",
      borderRadius: "8px",
      background: c.bg,
      color: c.text,
      textTransform: "capitalize" as const,
      flexShrink: 0,
    };
  },
  info: {
    minWidth: 0,
    flex: 1,
  },
  title: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.text,
    marginBottom: "2px",
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  meta: {
    fontSize: "11px",
    color: COLORS.textMuted,
    display: "flex",
    gap: "6px",
    flexWrap: "wrap" as const,
  },
  openArrow: {
    fontSize: "12px",
    color: COLORS.textSecondary,
    fontWeight: 600,
    flexShrink: 0,
  },
  empty: {
    padding: "16px",
    color: COLORS.textMuted,
    fontSize: "13px",
    textAlign: "center" as const,
  },
};

function formatTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

function formatDateLabel(iso: string | null): string {
  if (!iso) return "Unknown date";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function runLabel(iso: string | null): string {
  if (!iso) return "Run";
  return new Date(iso).getHours() < 12 ? "Morning run" : "Daily run";
}

export default function ReportHistory({ selectedEventId, onSelectEvent }: Props) {
  const [events, setEvents] = useState<IngestionEvent[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await listEvents(0, 50);
      setEvents(data.items);
    } catch {
      // keep previous list on error
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const groups = events.reduce<Record<string, IngestionEvent[]>>((acc, ev) => {
    const key = ev.triggered_at?.slice(0, 10) ?? "unknown";
    (acc[key] ??= []).push(ev);
    return acc;
  }, {});

  if (events.length === 0 && !loading) {
    return <div style={S.empty}>No reports yet. Ingest data to generate reports.</div>;
  }

  return (
    <div style={S.wrap}>
      {Object.entries(groups).map(([date, items]) => (
        <div key={date} style={S.dateGroup}>
          <div style={S.groupTitle}>{formatDateLabel(items[0]?.triggered_at ?? null)}</div>
          {items.map((event) => {
            const batches = event.batches ?? [];
            const deepCount = batches.filter((b) => b.deep_analysis_status !== "queued").length;
            const fastCount = batches.filter((b) => b.deep_analysis_status === "queued").length;
            const showCounts = batches.length > 0;

            return (
              <div
                key={event.event_id}
                style={S.row(event.event_id === selectedEventId)}
                onClick={() => onSelectEvent(event)}
              >
                <div style={S.left}>
                  <span style={S.badge(event.status)}>{event.status}</span>
                  <div style={S.info}>
                    <div style={S.title}>{runLabel(event.triggered_at)}</div>
                    <div style={S.meta}>
                      <span>{event.machines_found} machines</span>
                      {showCounts && deepCount > 0 && (
                        <span>⭐ {deepCount} Full AI</span>
                      )}
                      {showCounts && fastCount > 0 && (
                        <span>⚡ {fastCount} Fast</span>
                      )}
                      <span>{formatTime(event.triggered_at)}</span>
                    </div>
                  </div>
                </div>
                <span style={S.openArrow}>→</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
