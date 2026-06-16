import { API_BASE_URL } from "../config";
import type { ChatMessage, ChatResponse, PlantOverview, SessionContext } from "../types";

export async function sendChatQuery(
  query: string,
  sessionContext?: SessionContext
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: query.trim(),
      session_context: sessionContext ?? { last_machine_id: null, last_intent: null },
    }),
  });

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return data as ChatResponse;
}

export async function getChatHistory(limit = 50): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat/history?limit=${limit}`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (data.messages ?? []) as ChatMessage[];
}

export async function getPlantOverview(): Promise<PlantOverview> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat/plant-overview`);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return data as PlantOverview;
}
