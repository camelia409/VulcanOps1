import { useEffect, useState } from "react";
import type { ChatResponse, IngestionStatus, PlantOverview, SessionContext } from "../../types";
import { getPlantOverview } from "../../api/chatApi";
import ChatPanel from "../chat/ChatPanel";
import CopilotPanel from "../chat/CopilotPanel";
import { COLORS } from "../../theme";

interface Props {
  pipelineStatus: IngestionStatus;
  chatDisabled: boolean;
}

const S = {
  page: {
    display: "grid",
    gridTemplateColumns: "minmax(320px, 1fr) minmax(420px, 1.4fr)",
    gap: "20px",
    height: "100%",
    overflow: "hidden",
  },
  card: {
    background: COLORS.cardBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "12px",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column" as const,
  },
  lockedWrap: {
    height: "100%",
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    gap: "16px",
    padding: "40px",
    textAlign: "center" as const,
  },
  lockedIcon: { fontSize: "40px", opacity: 0.35 },
  lockedTitle: { fontSize: "16px", fontWeight: 600, color: COLORS.text },
  lockedBody: {
    fontSize: "13px",
    color: COLORS.textMuted,
    maxWidth: "360px",
    lineHeight: 1.6,
  },
  lockedBadge: (status: IngestionStatus): React.CSSProperties => {
    const map: Partial<Record<IngestionStatus, { bg: string; text: string }>> = {
      pending:    { bg: "#fef3c7", text: "#d97706" },
      running:    { bg: "#dbeafe", text: "#2563eb" },
      processing: { bg: "#ede9fe", text: "#6d28d9" },
      failed:     { bg: "#fee2e2", text: "#dc2626" },
    };
    const c = map[status] ?? { bg: COLORS.inputBg, text: COLORS.textMuted };
    return {
      display: "inline-block",
      fontSize: "11px",
      fontWeight: 700,
      padding: "4px 12px",
      borderRadius: "12px",
      background: c.bg,
      color: c.text,
      textTransform: "uppercase" as const,
      letterSpacing: "0.08em",
    };
  },
};

const LOCKED_MESSAGES: Partial<Record<IngestionStatus, { title: string; body: string }>> = {
  pending: {
    title: "No data ingested yet",
    body: "Upload your machine registry, sensor readings, and maintenance files to start.",
  },
  running: {
    title: "Ingesting files…",
    body: "Files are being validated and stored. Agent processing will begin shortly.",
  },
  processing: {
    title: "AI agents are processing data…",
    body: "The pipeline is running: Anomaly Detection → Diagnosis → Verification → Reports. Copilot will be available once processing completes.",
  },
  failed: {
    title: "Pipeline failed",
    body: "The last processing run encountered errors. Re-upload affected files and try again.",
  },
};

function LockedChat({ pipelineStatus }: { pipelineStatus: IngestionStatus }) {
  const msg = LOCKED_MESSAGES[pipelineStatus] ?? {
    title: "Copilot unavailable",
    body: "Ingest data and wait for processing to complete.",
  };
  return (
    <div style={S.lockedWrap}>
      <div style={S.lockedIcon}>🔒</div>
      <div style={S.lockedTitle}>{msg.title}</div>
      <div style={S.lockedBody}>{msg.body}</div>
      <span style={S.lockedBadge(pipelineStatus)}>{pipelineStatus}</span>
    </div>
  );
}

export default function ChatTab({ pipelineStatus, chatDisabled }: Props) {
  const [result, setResult] = useState<ChatResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [plantOverview, setPlantOverview] = useState<PlantOverview | null>(null);
  const [sessionContext, setSessionContext] = useState<SessionContext>({
    last_machine_id: null,
    last_intent: null,
  });

  useEffect(() => {
    if (!chatDisabled) {
      getPlantOverview()
        .then(setPlantOverview)
        .catch(() => null);
    }
  }, [chatDisabled]);

  const handleResult = (res: ChatResponse) => {
    setResult(res);
    const firstMachineId = res.reports?.[0]?.machine?.machine_id ?? null;
    setSessionContext({
      last_machine_id: firstMachineId ?? sessionContext.last_machine_id,
      last_intent: res.intent,
    });
    // Refresh plant overview after plant_summary queries
    if (res.plant_overview) {
      setPlantOverview(res.plant_overview);
    }
  };

  if (chatDisabled) {
    return <LockedChat pipelineStatus={pipelineStatus} />;
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <ChatPanel
          onResult={handleResult}
          onLoading={setLoading}
          loading={loading}
          sessionContext={sessionContext}
        />
      </div>
      <div style={S.card}>
        <CopilotPanel
          result={result}
          loading={loading}
          plantOverview={plantOverview}
        />
      </div>
    </div>
  );
}
