import { useState } from "react";
import type { IngestionEvent, ReportBatchSummary } from "../../types";
import { getEvent } from "../../api/reportsApi";
import MachineList from "../reports/MachineList";
import ReportHistory from "../reports/ReportHistory";
import ReportViewer from "../reports/ReportViewer";
import { COLORS } from "../../theme";

const S = {
  page: {
    display: "grid",
    gridTemplateColumns: "minmax(300px, 360px) 1fr",
    gap: "20px",
    height: "100%",
    overflow: "hidden",
  },
  leftCard: {
    background: COLORS.cardBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    padding: "16px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "0",
    overflow: "hidden",
  },
  rightCard: {
    background: COLORS.cardBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column" as const,
  },
  sectionTitle: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textMuted,
    textTransform: "uppercase" as const,
    letterSpacing: "0.07em",
    marginBottom: "8px",
    flexShrink: 0,
  },
  // Top section (history): fixed height portion of leftCard
  historySection: {
    display: "flex",
    flexDirection: "column" as const,
    flex: "0 0 40%",
    minHeight: 0,
    overflow: "hidden",
    paddingBottom: "12px",
  },
  // Bottom section (machines): takes remaining space
  machinesSection: {
    display: "flex",
    flexDirection: "column" as const,
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
    borderTop: `1px solid ${COLORS.border}`,
    paddingTop: "12px",
  },
  scroll: {
    flex: 1,
    overflowY: "auto" as const,
    minHeight: 0,
  },
};

// Auto-select a batch when an event is opened: prefer Full AI, highest risk first.
function pickFirstBatch(event: IngestionEvent): string | null {
  const batches = event.batches ?? [];
  if (batches.length === 0) return null;
  const sorted = [...batches].sort((a, b) => {
    const aDeep = a.deep_analysis_status !== "queued" ? 0 : 1;
    const bDeep = b.deep_analysis_status !== "queued" ? 0 : 1;
    if (aDeep !== bDeep) return aDeep - bDeep;
    return (b.risk_score ?? 0) - (a.risk_score ?? 0);
  });
  return sorted[0].batch_id;
}

async function fetchEvent(eventId: string): Promise<IngestionEvent | null> {
  try {
    return await getEvent(eventId);
  } catch {
    return null;
  }
}

export default function ReportsTab() {
  const [selectedEvent, setSelectedEvent] = useState<IngestionEvent | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<string | null>(null);

  const handleSelectEvent = async (event: IngestionEvent) => {
    setSelectedBatchId(null);
    const full = await fetchEvent(event.event_id);
    const resolved = full ?? event;
    setSelectedEvent(resolved);
    const firstId = pickFirstBatch(resolved);
    if (firstId) setSelectedBatchId(firstId);
  };

  const handleSelectBatch = (batch: ReportBatchSummary) => {
    setSelectedBatchId(batch.batch_id);
  };

  // Called by ReportViewer after a successful deep analysis run.
  // Updates the selected batch to the newly-analysed one, then refreshes
  // the event so MachineList shows the updated deep_analysis_status.
  const handleDeepAnalysisComplete = async (newBatchId: string) => {
    setSelectedBatchId(newBatchId);
    if (selectedEvent) {
      const refreshed = await fetchEvent(selectedEvent.event_id);
      if (refreshed) setSelectedEvent(refreshed);
    }
  };

  return (
    <div style={S.page}>
      <div style={S.leftCard}>
        {/* History section */}
        <div style={S.historySection}>
          <div style={S.sectionTitle}>Report history</div>
          <div style={S.scroll}>
            <ReportHistory
              selectedEventId={selectedEvent?.event_id}
              onSelectEvent={handleSelectEvent}
            />
          </div>
        </div>

        {/* Machine list section */}
        <div style={S.machinesSection}>
          <div style={S.sectionTitle}>Machines</div>
          <div style={S.scroll}>
            <MachineList
              event={selectedEvent}
              selectedBatchId={selectedBatchId ?? undefined}
              onSelectBatch={handleSelectBatch}
            />
          </div>
        </div>
      </div>

      <div style={S.rightCard}>
        <ReportViewer
          batchId={selectedBatchId}
          onDeepAnalysisComplete={handleDeepAnalysisComplete}
        />
      </div>
    </div>
  );
}
