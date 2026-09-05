/**
 * Streams POST /api/chats/{chatId}/messages as Server-Sent Events using a
 * manual fetch + ReadableStream reader, NOT the browser EventSource API -
 * EventSource only supports GET and can't attach a custom Authorization
 * header, but this endpoint is bearer-token protected like every other
 * /api/* call in this app. This yields real incremental tokens as they
 * arrive from the backend, not a fake progress bar.
 *
 * A single retry-after-refresh (mirroring api.ts's request()) is not
 * implemented here for simplicity - if the access token has expired,
 * onError() fires with a 401 and the caller can prompt the user to
 * re-login rather than silently retrying mid-stream.
 */

import { apiUrl } from "./config";
import { getAccessToken } from "./auth";

export type StreamCallbacks = {
  onToken: (text: string) => void;
  onError: (message: string) => void;
  onDone: () => void;
};

// "all" (default): the backend retrieves from every document the current
// user has uploaded, across all of their chats. "chat": restrict
// retrieval to just this chat's uploads - wired to the "Only search this
// chat's documents" checkbox in AppShellPage.
export type MessageScope = "all" | "chat";

export async function streamChatMessage(
  chatId: number,
  content: string,
  scope: MessageScope,
  callbacks: StreamCallbacks,
): Promise<void> {
  const token = getAccessToken();

  let res: Response;
  try {
    res = await fetch(apiUrl(`/api/chats/${chatId}/messages`), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ content, scope }),
    });
  } catch {
    callbacks.onError("Could not reach the server.");
    return;
  }

  if (!res.ok || !res.body) {
    callbacks.onError(await describeError(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex = buffer.indexOf("\n\n");
    while (separatorIndex !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);
      handleEvent(rawEvent, callbacks);
      separatorIndex = buffer.indexOf("\n\n");
    }
  }
}

async function describeError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && typeof detail.message === "string") {
      return detail.message;
    }
  } catch {
    // fall through to the generic message below
  }
  return `Request failed with status ${res.status}`;
}

function handleEvent(rawEvent: string, callbacks: StreamCallbacks): void {
  let eventName = "message";
  let data = "";
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice("event:".length).trim();
    else if (line.startsWith("data:")) data += line.slice("data:".length).trim();
  }
  if (!data) return;

  let parsed: { content?: string; message?: string };
  try {
    parsed = JSON.parse(data);
  } catch {
    return;
  }

  if (eventName === "token" && parsed.content) {
    callbacks.onToken(parsed.content);
  } else if (eventName === "error") {
    callbacks.onError(parsed.message ?? "The assistant encountered an error.");
  } else if (eventName === "done") {
    callbacks.onDone();
  }
}
