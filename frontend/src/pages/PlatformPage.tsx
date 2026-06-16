import React, { useEffect, useState } from "react";
import { getIngestStatus } from "../api/ingestApi";
import DataIngestionTab from "../components/tabs/DataIngestionTab";
import ChatTab from "../components/tabs/ChatTab";
import ReportsTab from "../components/tabs/ReportsTab";
import { COLORS } from "../theme";
import type { IngestionStatus } from "../types";

type Tab = "ingest" | "chat" | "reports";

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "ingest", label: "Data ingestion", icon: "↑" },
  { key: "chat", label: "Chat", icon: "✉" },
  { key: "reports", label: "Reports", icon: "🗎" },
];

const S = {
  page: {
    height: "100vh",
    display: "flex",
    flexDirection: "column" as const,
    background: COLORS.pageBg,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 24px",
    height: "56px",
    borderBottom: `1px solid ${COLORS.border}`,
    background: COLORS.headerBg,
    flexShrink: 0,
  },
  brand: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
  },
  logo: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    fontSize: "15px",
    fontWeight: 700,
    color: COLORS.text,
    letterSpacing: "-0.01em",
  },
  logoIcon: {
    width: "22px",
    height: "22px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: COLORS.accent,
    fontSize: "16px",
  },
  subtitle: {
    fontSize: "12px",
    color: COLORS.textMuted,
    fontWeight: 500,
  },
  tabBar: {
    display: "flex",
    gap: "8px",
  },
  tab: (active: boolean, disabled: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "0 16px",
    height: "36px",
    background: active ? COLORS.cardBg : "transparent",
    border: `1px solid ${active ? COLORS.borderStrong : "transparent"}`,
    borderBottom: active ? `2px solid ${COLORS.accent}` : "2px solid transparent",
    borderRadius: "6px 6px 0 0",
    color: disabled ? COLORS.textLight : active ? COLORS.accentText : COLORS.textSecondary,
    fontSize: "13px",
    fontWeight: 600,
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.6 : 1,
  }),
  content: {
    flex: 1,
    overflow: "hidden",
    padding: "20px",
  },
  processingBadge: {
    fontSize: "11px",
    color: COLORS.running,
    background: COLORS.runningBg,
    padding: "4px 8px",
    borderRadius: "12px",
    fontWeight: 600,
  },
};

export default function PlatformPage() {
  const [activeTab, setActiveTab] = useState<Tab>("ingest");
  const [pipelineStatus, setPipelineStatus] = useState<IngestionStatus>("pending");

  useEffect(() => {
    const load = async () => {
      try {
        const status = await getIngestStatus();
        setPipelineStatus(status.status);
      } catch {
        // leave previous status
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  // Chat is only available when the latest pipeline run completed successfully.
  const chatDisabled = pipelineStatus !== "done";

  const STATUS_LABEL: Partial<Record<IngestionStatus, string>> = {
    pending:    "No data ingested",
    running:    "Ingesting files…",
    processing: "Agents processing…",
    failed:     "Pipeline failed",
  };
  const statusLabel = STATUS_LABEL[pipelineStatus] ?? null;

  const processingBadgeColor: React.CSSProperties =
    pipelineStatus === "failed"
      ? { color: "#dc2626", background: "#fee2e2" }
      : pipelineStatus === "done"
      ? { color: "#16a34a", background: "#dcfce7" }
      : { color: "#6d28d9", background: "#ede9fe" };

  return (
    <div style={S.page}>
      <header style={S.header}>
        <div style={S.brand}>
          <div style={S.logo}>
            <span style={S.logoIcon}>🔥</span>
            <span>VulcanOps</span>
          </div>
          <div style={S.subtitle}>Autonomous Reliability Operations</div>
        </div>

        {statusLabel && (
          <div style={{ ...S.processingBadge, ...processingBadgeColor }}>
            {statusLabel}
          </div>
        )}

        <nav style={S.tabBar}>
          {TABS.map((t) => {
            const disabled = t.key === "chat" && chatDisabled;
            return (
              <button
                key={t.key}
                style={S.tab(activeTab === t.key, disabled)}
                onClick={() => {
                  if (!disabled) setActiveTab(t.key);
                }}
                disabled={disabled}
                title={
                  disabled
                    ? "Chat becomes available once the current pipeline run is complete."
                    : undefined
                }
              >
                <span style={{ fontSize: "12px" }}>{t.icon}</span>
                <span>{t.label}</span>
              </button>
            );
          })}
        </nav>
      </header>

      <main style={S.content}>
        {activeTab === "ingest" && <DataIngestionTab />}
        {activeTab === "chat" && (
          <ChatTab pipelineStatus={pipelineStatus} chatDisabled={chatDisabled} />
        )}
        {activeTab === "reports" && <ReportsTab />}
      </main>
    </div>
  );
}
