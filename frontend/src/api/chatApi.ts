import { API_BASE_URL } from "../config";
import type { ChatMessage, ChatResponse, PlantOverview, SessionContext } from "../types";

async function parseErrorBody(res: Response): Promise<{ detail?: string }> {
  return res.json().catch(() => ({}));
}

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

  if (!res.ok) {
    const data = await parseErrorBody(res);
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as ChatResponse;
}

export async function getChatHistory(limit = 50): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat/history?limit=${limit}`);
  if (!res.ok) {
    const data = await parseErrorBody(res);
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  const data = await res.json();
  return (data.messages ?? []) as ChatMessage[];
}

export async function getPlantOverview(): Promise<PlantOverview> {
  const res = await fetch(`${API_BASE_URL}/api/v1/chat/plant-overview`);
  if (!res.ok) {
    const data = await parseErrorBody(res);
    throw new Error(data.detail ?? `Server error ${res.status}`);
  }
  return (await res.json()) as PlantOverview;
}
