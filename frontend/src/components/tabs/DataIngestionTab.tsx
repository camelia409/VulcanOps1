import { useState } from "react";
import type { IngestResponse } from "../../types";
import IngestionHistory from "../ingestion/IngestionHistory";
import PipelineStatus from "../ingestion/PipelineStatus";
import UploadPanel from "../ingestion/UploadPanel";
import { COLORS } from "../../theme";

const S = {
  page: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "20px",
    height: "100%",
    overflow: "hidden",
  },
  topRow: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "20px",
    flex: 1,
    minHeight: 0,
  },
  card: {
    background: COLORS.cardBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    padding: "20px",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
  },
  cardTitle: {
    fontSize: "14px",
    fontWeight: 600,
    color: COLORS.text,
    marginBottom: "16px",
  },
};

export default function DataIngestionTab() {
  const [refreshKey, setRefreshKey] = useState(0);

  const handleIngestStarted = (response: IngestResponse) => {
    if (response.event_id) {
      setRefreshKey((k) => k + 1);
    }
  };

  return (
    <div style={S.page}>
      <div style={S.topRow}>
        <div style={S.card}>
          <div style={S.cardTitle}>Upload files</div>
          <UploadPanel onIngestStarted={handleIngestStarted} />
        </div>

        <div style={S.card}>
          <div style={S.cardTitle}>Ingestion history</div>
          <IngestionHistory key={refreshKey} />
        </div>
      </div>

      <PipelineStatus />
    </div>
  );
}
