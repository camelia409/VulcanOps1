import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ChatResponse, SessionContext } from "../../types";
import { getChatHistory, sendChatQuery } from "../../api/chatApi";
import { COLORS } from "../../theme";

interface Props {
  onResult: (result: ChatResponse) => void;
  onLoading: (loading: boolean) => void;
  loading: boolean;
  sessionContext: SessionContext;
}

const SUGGESTION_CHIPS = [
  { label: "Highest Risk", query: "Which machine is at highest risk?" },
  { label: "Top 3 Priority", query: "Show top 3 priority machines" },
  { label: "Low Confidence", query: "Which machines have low confidence diagnoses?" },
  { label: "Emergency Machines", query: "Show emergency machines" },
  { label: "Plant Summary", query: "Plant overview" },
];

const S = {
  wrap: {
    display: "flex",
    flexDirection: "column" as const,
    height: "100%",
  },
  header: {
    padding: "14px 16px",
    borderBottom: `1px solid ${COLORS.border}`,
    flexShrink: 0,
  },
  headerTitle: {
    fontSize: "13px",
    fontWeight: 700,
    color: COLORS.text,
    marginBottom: "2px",
  },
  headerSub: {
    fontSize: "11px",
    color: COLORS.textMuted,
  },
  messages: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "14px 16px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "10px",
    minHeight: 0,
  },
  emptyState: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "6px",
    padding: "8px 0",
  },
  emptyTitle: {
    fontSize: "13px",
    fontWeight: 600,
    color: COLORS.text,
  },
  emptyBody: {
    fontSize: "12px",
    color: COLORS.textMuted,
    lineHeight: 1.6,
  },
  bubbleUser: {
    alignSelf: "flex-end" as const,
    maxWidth: "88%",
    background: COLORS.accent,
    borderRadius: "12px 12px 2px 12px",
    padding: "9px 13px",
    fontSize: "13px",
    color: "#fff",
    lineHeight: 1.5,
  },
  bubbleAssistant: {
    alignSelf: "flex-start" as const,
    maxWidth: "88%",
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "2px 12px 12px 12px",
    padding: "9px 13px",
    fontSize: "13px",
    color: COLORS.text,
    lineHeight: 1.5,
  },
  bubbleLabel: {
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    marginBottom: "4px",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
  },
  cacheHit: {
    fontSize: "10px",
    color: "#16a34a",
    marginTop: "3px",
  },
  inputArea: {
    padding: "10px 14px 14px",
    borderTop: `1px solid ${COLORS.border}`,
    flexShrink: 0,
  },
  chipRow: {
    display: "flex",
    gap: "5px",
    overflowX: "auto" as const,
    marginBottom: "8px",
    paddingBottom: "2px",
  },
  chip: (disabled: boolean): React.CSSProperties => ({
    whiteSpace: "nowrap" as const,
    fontSize: "11px",
    fontWeight: 600,
    color: disabled ? COLORS.textMuted : COLORS.accent,
    background: disabled ? COLORS.inputBg : COLORS.accentLight,
    border: `1px solid ${disabled ? COLORS.border : COLORS.accent}`,
    borderRadius: "12px",
    padding: "4px 10px",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.6 : 1,
    flexShrink: 0,
  }),
  inputRow: {
    display: "flex",
    gap: "8px",
    alignItems: "flex-end",
  },
  textarea: {
    flex: 1,
    background: COLORS.inputBg,
    border: `1px solid ${COLORS.border}`,
    borderRadius: "10px",
    color: COLORS.text,
    fontSize: "13px",
    padding: "10px 12px",
    resize: "none" as const,
    outline: "none",
    lineHeight: 1.5,
    minHeight: "44px",
    maxHeight: "100px",
  },
  sendBtn: (disabled: boolean): React.CSSProperties => ({
    width: "40px",
    height: "40px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: "10px",
    border: "none",
    background: disabled ? COLORS.border : COLORS.accent,
    color: "#fff",
    fontSize: "16px",
    cursor: disabled ? "not-allowed" : "pointer",
    flexShrink: 0,
  }),
  errorBox: {
    marginTop: "6px",
    color: COLORS.failed,
    fontSize: "12px",
  },
};

function assistantSummary(msg: ChatMessage): { text: string; cacheHit: boolean | undefined } {
  const resp = msg.response_json;
  if (!resp) return { text: "Thinking…", cacheHit: undefined };

  // If LLM generated a copilot answer, prefer that
  if (resp.copilot_answer) {
    return { text: resp.copilot_answer, cacheHit: resp.cache_hit };
  }

  if (resp.plant_overview) {
    const ov = resp.plant_overview;
    return {
      text: `${ov.total_machines} machines — ${ov.emergency_count} Emergency, ${ov.urgent_count} Urgent, ${ov.routine_count} Routine.`,
      cacheHit: true,
    };
  }

  if (resp.reports?.length) {
    const r = resp.reports[0];
    const name = r.machine?.machine_name ?? "Machine";
    const priority = r.priority ?? "—";
    const rul = r.rul_hours != null ? ` · RUL ${r.rul_hours.toFixed(0)}h` : "";
    return { text: `${name} · ${priority}${rul}`, cacheHit: resp.cache_hit };
  }

  if (resp.machines?.length) {
    return {
      text: `${resp.title} — ${resp.machines.length} machine(s) found.`,
      cacheHit: resp.cache_hit,
    };
  }

  return { text: resp.title || "Done", cacheHit: resp.cache_hit };
}

export default function ChatPanel({ onResult, onLoading, loading, sessionContext }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getChatHistory(50)
      .then((history) => setMessages(history))
      .catch(() => setMessages([]));
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || loading) return;

    setError(null);
    onLoading(true);

    const userMsg: ChatMessage = {
      message_id: `temp-u-${Date.now()}`,
      role: "user",
      query: trimmed,
      response_json: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setQuery("");

    try {
      const data = await sendChatQuery(trimmed, sessionContext);
      onResult(data);
      const assistantMsg: ChatMessage = {
        message_id: `temp-a-${Date.now()}`,
        role: "assistant",
        query: trimmed,
        response_json: data,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Network error");
      onLoading(false);
    } finally {
      onLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(query);
    }
  };

  return (
    <div style={S.wrap}>
      <div style={S.header}>
        <div style={S.headerTitle}>Industrial Copilot Workspace</div>
        <div style={S.headerSub}>Reads from agent report cache — never re-runs analysis</div>
      </div>

      <div style={S.messages}>
        {messages.length === 0 && (
          <div style={S.emptyState}>
            <div style={S.emptyTitle}>Ask about your plant</div>
            <div style={S.emptyBody}>
              Use the chips below or type a question. The copilot reads
              from pre-generated agent reports — no pipelines re-run.
            </div>
          </div>
        )}
        {messages.map((msg) =>
          msg.role === "user" ? (
            <div key={msg.message_id} style={S.bubbleUser}>
              {msg.query}
            </div>
          ) : (
            <div key={msg.message_id} style={S.bubbleAssistant}>
              <div style={S.bubbleLabel}>Copilot</div>
              {(() => {
                const { text, cacheHit } = assistantSummary(msg);
                return (
                  <>
                    <div>{text || "Done"}</div>
                    {cacheHit === true && (
                      <div style={S.cacheHit}>Cache hit</div>
                    )}
                  </>
                );
              })()}
            </div>
          )
        )}
        {loading && (
          <div style={{ ...S.bubbleAssistant, opacity: 0.6 }}>
            <div style={S.bubbleLabel}>Copilot</div>
            <div>Reading report cache…</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div style={S.inputArea}>
        <div style={S.chipRow}>
          {SUGGESTION_CHIPS.map((chip) => (
            <button
              key={chip.label}
              style={S.chip(loading)}
              onClick={() => submit(chip.query)}
              disabled={loading}
            >
              {chip.label}
            </button>
          ))}
        </div>

        <div style={S.inputRow}>
          <textarea
            style={S.textarea}
            placeholder="Ask about machines, risk, RUL, or plant health…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            rows={1}
          />
          <button
            style={S.sendBtn(loading || !query.trim())}
            disabled={loading || !query.trim()}
            onClick={() => submit(query)}
          >
            ➤
          </button>
        </div>
        {error && <div style={S.errorBox}>{error}</div>}
      </div>
    </div>
  );
}
