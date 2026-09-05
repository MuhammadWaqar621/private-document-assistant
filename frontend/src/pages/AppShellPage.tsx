import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../lib/api";
import { clearTokens, setTokens } from "../lib/auth";
import type { Chat, ChatDetail, CurrentUser } from "../lib/types";

/**
 * Bare-bones chat shell: proves auth + chat list + chat history plumbing
 * works end-to-end. Sending a message and getting an AI response is NOT
 * wired up here - that's the document pipeline + RAG streaming phase.
 */
export default function AppShellPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [selectedChat, setSelectedChat] = useState<ChatDetail | null>(null);
  const [loadingChats, setLoadingChats] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Google OAuth redirects back here as /app?access_token=...&refresh_token=...
  // Consume them once, then strip from the URL so a refresh/bookmark
  // doesn't resubmit stale tokens.
  const consumedOAuthTokens = useRef(false);
  useEffect(() => {
    if (consumedOAuthTokens.current) return;
    const accessToken = searchParams.get("access_token");
    const refreshToken = searchParams.get("refresh_token");
    if (accessToken && refreshToken) {
      setTokens(accessToken, refreshToken);
      consumedOAuthTokens.current = true;
      navigate("/app", { replace: true });
    }
  }, [searchParams, navigate]);

  const handleAuthFailure = useCallback(() => {
    clearTokens();
    navigate("/login", { replace: true });
  }, [navigate]);

  const loadChats = useCallback(async () => {
    setLoadingChats(true);
    try {
      const [me, chatList] = await Promise.all([
        api.get<CurrentUser>("/api/auth/me", true),
        api.get<Chat[]>("/api/chats", true),
      ]);
      setUser(me);
      setChats(chatList);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load chats.");
    } finally {
      setLoadingChats(false);
    }
  }, [handleAuthFailure]);

  useEffect(() => {
    loadChats();
  }, [loadChats]);

  async function selectChat(chatId: number) {
    setLoadingMessages(true);
    try {
      const detail = await api.get<ChatDetail>(`/api/chats/${chatId}`, true);
      setSelectedChat(detail);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load chat.");
    } finally {
      setLoadingMessages(false);
    }
  }

  async function createChat() {
    setCreating(true);
    try {
      const chat = await api.post<Chat>("/api/chats", {}, true);
      setChats((prev) => [chat, ...prev]);
      await selectChat(chat.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to create chat.");
    } finally {
      setCreating(false);
    }
  }

  async function deleteChat(chatId: number, event: MouseEvent) {
    event.stopPropagation();
    try {
      await api.del(`/api/chats/${chatId}`, true);
      setChats((prev) => prev.filter((c) => c.id !== chatId));
      setSelectedChat((prev) => (prev?.id === chatId ? null : prev));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to delete chat.");
    }
  }

  function logout() {
    clearTokens();
    navigate("/login", { replace: true });
  }

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      <aside className="flex w-72 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-4">
          <span className="text-lg font-bold tracking-tight">querynest</span>
        </div>

        <div className="p-3">
          <button
            type="button"
            onClick={createChat}
            disabled={creating}
            className="w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-50"
          >
            {creating ? "Creating..." : "+ New chat"}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-2">
          {loadingChats && <p className="px-2 py-2 text-sm text-slate-400">Loading chats...</p>}
          {!loadingChats && chats.length === 0 && (
            <p className="px-2 py-2 text-sm text-slate-400">No chats yet - create one above.</p>
          )}
          {chats.map((chat) => (
            <button
              key={chat.id}
              type="button"
              onClick={() => selectChat(chat.id)}
              className={`group mb-1 flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition ${
                selectedChat?.id === chat.id
                  ? "bg-slate-100 font-medium"
                  : "hover:bg-slate-50"
              }`}
            >
              <span className="truncate">{chat.title}</span>
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => deleteChat(chat.id, e)}
                className="ml-2 hidden shrink-0 text-xs text-slate-400 hover:text-red-600 group-hover:inline"
              >
                Delete
              </span>
            </button>
          ))}
        </div>

        <div className="border-t border-slate-200 p-3">
          <p className="truncate px-1 text-xs text-slate-500">{user?.email ?? "..."}</p>
          <button
            type="button"
            onClick={logout}
            className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
          >
            Log out
          </button>
        </div>
      </aside>

      <main className="flex flex-1 flex-col">
        {error && (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
            {error}
          </div>
        )}

        {!selectedChat && (
          <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
            Select a chat on the left, or create a new one.
          </div>
        )}

        {selectedChat && (
          <>
            <div className="border-b border-slate-200 px-6 py-4">
              <h2 className="font-semibold">{selectedChat.title}</h2>
            </div>

            <div className="flex-1 overflow-y-auto px-6 py-4">
              {loadingMessages && <p className="text-sm text-slate-400">Loading messages...</p>}
              {!loadingMessages && selectedChat.messages.length === 0 && (
                <p className="text-sm text-slate-400">
                  No messages yet in this chat.
                </p>
              )}
              <div className="flex flex-col gap-3">
                {selectedChat.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`max-w-2xl rounded-lg px-4 py-2 text-sm ${
                      message.role === "user"
                        ? "ml-auto bg-slate-900 text-white"
                        : "bg-white text-slate-900 shadow-sm"
                    }`}
                  >
                    {message.content}
                  </div>
                ))}
              </div>
            </div>

            <div className="border-t border-slate-200 p-4">
              <div className="flex gap-2">
                <input
                  type="text"
                  disabled
                  placeholder="Sending messages isn't wired up yet - coming in the next phase."
                  className="flex-1 cursor-not-allowed rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-sm text-slate-400"
                />
                <button
                  type="button"
                  disabled
                  className="cursor-not-allowed rounded-lg bg-slate-200 px-4 py-2 text-sm font-medium text-slate-400"
                >
                  Send
                </button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
