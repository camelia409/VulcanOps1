import { useEffect, useState } from "react";
import type { IngestionEvent } from "../../types";
import { listEvents } from "../../api/reportsApi";

interface Props {
  selectedEventId?: string;
  onSelectEvent: (event: IngestionEvent) => void;
}

const REFRESH_INTERVAL_MS = 5000;

const S = {
  wrap: { display: "flex", flexDirection: "column" as const, gap: "1px" },
  header: {
    padding: "12px 16px",
    background: "#0f0f0f",
    borderBottom: "1px solid #1f1f1f",
    fontSize: "10px",
    letterSpacing: "0.14em",
    color: "#525252",
    textTransform: "uppercase" as const,
    fontWeight: 600,
  },
  row: (active: boolean): React.CSSProperties => ({
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
  eventId: {
    fontSize: "11px",
    color: "#737373",
    fontFamily: "monospace",
  },
  status: (status: string): React.CSSProperties => ({
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.08em",
    textTransform: "uppercase" as const,
    color:
      status === "done"
        ? "#16a34a"
        : status === "failed"
        ? "#dc2626"
        : status === "running"
        ? "#f97316"
        : "#525252",
  }),
  meta: {
    fontSize: "12px",
    color: "#a3a3a3",
  },
  empty: {
    padding: "24px 16px",
    textAlign: "center" as const,
    color: "#404040",
    fontSize: "13px",
  },
  error: {
    padding: "12px 16px",
    color: "#dc2626",
    fontSize: "12px",
    background: "#1a0000",
  },
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export default function ReportEventList({
  selectedEventId,
  onSelectEvent,
}: Props) {
  const [events, setEvents] = useState<IngestionEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await listEvents(0, 50);
      setEvents(data.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load events");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div style={S.header}>Ingestion Events</div>
      {error && <div style={S.error}>{error}</div>}
      <div style={S.wrap}>
        {events.length === 0 && !error ? (
          <div style={S.empty}>No events yet.</div>
        ) : (
          events.map((event) => (
            <div
              key={event.event_id}
              style={S.row(event.event_id === selectedEventId)}
              onClick={() => onSelectEvent(event)}
            >
              <div style={S.rowTop}>
                <span style={S.eventId}>{event.event_id.slice(0, 8)}…</span>
                <span style={S.status(event.status)}>{event.status}</span>
              </div>
              <div style={S.meta}>
                {event.machines_found} machine(s) · {formatDate(event.triggered_at)}
              </div>
            </div>
          ))
        )}
      </div>
      {loading && <div style={{ padding: "8px 16px", color: "#525252", fontSize: "11px" }}>Loading…</div>}
    </div>
  );
}
