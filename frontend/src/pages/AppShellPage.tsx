import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { useNavigate } from "react-router-dom";

import DocumentUpload from "../components/DocumentUpload";
import { ApiError, api } from "../lib/api";
import { clearTokens, setTokens } from "../lib/auth";
import { streamChatMessage } from "../lib/chatStream";
import { useConfigStatus } from "../lib/useConfigStatus";
import type { Chat, ChatDetail, CurrentUser, DocumentOut } from "../lib/types";

/**
 * Main chat shell: chat list + history (Phase 2) plus, as of this phase,
 * document upload and a real streaming RAG chat - sending a message calls
 * POST /api/chats/{id}/messages and renders the assistant's reply
 * incrementally as Server-Sent Events arrive (see lib/chatStream.ts),
 * rather than waiting for the whole answer and dumping it at once.
 */
export default function AppShellPage() {
  const navigate = useNavigate();
  const { status: configStatus } = useConfigStatus();

  const [user, setUser] = useState<CurrentUser | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [selectedChat, setSelectedChat] = useState<ChatDetail | null>(null);
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [loadingChats, setLoadingChats] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [messageInput, setMessageInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streamingReply, setStreamingReply] = useState<string | null>(null);
  // Unchecked by default: retrieval draws from every document the user has
  // uploaded across all of their chats. Checking this restricts retrieval
  // to just the currently-selected chat's uploads (scope: "chat" in the
  // POST body - see lib/chatStream.ts).
  const [chatScopeOnly, setChatScopeOnly] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Google OAuth redirects back here as /app#access_token=...&refresh_token=...
  // A URL fragment (not a query string) so the tokens are never sent to
  // any server - the browser keeps them client-side only. Consume them
  // once, then strip from the URL so a refresh/bookmark doesn't resubmit
  // stale tokens.
  const consumedOAuthTokens = useRef(false);
  useEffect(() => {
    if (consumedOAuthTokens.current) return;
    const hash = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const hashParams = new URLSearchParams(hash);
    const accessToken = hashParams.get("access_token");
    const refreshToken = hashParams.get("refresh_token");
    if (accessToken && refreshToken) {
      setTokens(accessToken, refreshToken);
      consumedOAuthTokens.current = true;
      navigate("/app", { replace: true });
    }
  }, [navigate]);

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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [selectedChat?.messages, streamingReply]);

  const loadDocuments = useCallback(
    async (chatId: number) => {
      try {
        const docs = await api.get<DocumentOut[]>(`/api/chats/${chatId}/documents`, true);
        setDocuments(docs);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          handleAuthFailure();
        }
        // Non-fatal otherwise - the chat itself still works without the
        // document list rendering.
      }
    },
    [handleAuthFailure],
  );

  async function selectChat(chatId: number) {
    setLoadingMessages(true);
    setStreamingReply(null);
    try {
      const detail = await api.get<ChatDetail>(`/api/chats/${chatId}`, true);
      setSelectedChat(detail);
      await loadDocuments(chatId);
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

  async function handleSendMessage(event: FormEvent) {
    event.preventDefault();
    const content = messageInput.trim();
    if (!content || !selectedChat || sending) return;

    const chatId = selectedChat.id;
    setSending(true);
    setStreamingReply("");
    setMessageInput("");
    setError(null);

    // Optimistic: show the user's own message immediately rather than
    // waiting for the stream to finish and a refetch to bring it back.
    setSelectedChat((prev) =>
      prev && prev.id === chatId
        ? {
            ...prev,
            messages: [
              ...prev.messages,
              {
                id: -Date.now(),
                role: "user",
                content,
                created_at: new Date().toISOString(),
              },
            ],
          }
        : prev,
    );

    // `finish` reloads the chat from the server (so the optimistic user
    // message and the streamed reply get replaced with the real,
    // persisted rows) and clears the in-progress UI state. It's called
    // from onDone when the stream completes normally, and unconditionally
    // after streamChatMessage() resolves as a fallback for the case where
    // the initial request itself failed and no "done" event was ever
    // sent - the `finished` guard makes it safe to call twice.
    let finished = false;
    const finish = async () => {
      if (finished) return;
      finished = true;
      try {
        const detail = await api.get<ChatDetail>(`/api/chats/${chatId}`, true);
        setSelectedChat(detail);
      } catch {
        // Keep the optimistic/streamed content on screen if the refetch
        // itself fails - not worth surfacing a second error.
      }
      setStreamingReply(null);
      setSending(false);
    };

    await streamChatMessage(chatId, content, chatScopeOnly ? "chat" : "all", {
      onToken: (text) => setStreamingReply((prev) => (prev ?? "") + text),
      onError: (message) => setError(message),
      onDone: finish,
    });

    await finish();
  }

  const azureConfigured = configStatus?.azure_ai ?? true; // avoid a flash of "disabled" while loading
  const chatInputDisabled = !selectedChat || sending || !azureConfigured;

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
        {configStatus && !configStatus.azure_ai && (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
            Configuration missing - set Azure OpenAI credentials
            (AZURE_EM_*/LLM_ENDPOINT_MINI_MODEL*) in .env to enable document
            upload and chat.
          </div>
        )}

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

            <DocumentUpload
              chatId={selectedChat.id}
              documents={documents}
              onUploaded={(doc) => setDocuments((prev) => [doc, ...prev])}
              onAuthFailure={handleAuthFailure}
              disabled={!azureConfigured}
            />

            <div className="flex-1 overflow-y-auto px-6 py-4">
              {loadingMessages && <p className="text-sm text-slate-400">Loading messages...</p>}
              {!loadingMessages && selectedChat.messages.length === 0 && streamingReply === null && (
                <p className="text-sm text-slate-400">
                  No messages yet in this chat. Upload a document above, then
                  ask a question about it.
                </p>
              )}
              <div className="flex flex-col gap-3">
                {selectedChat.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`max-w-2xl whitespace-pre-wrap rounded-lg px-4 py-2 text-sm ${
                      message.role === "user"
                        ? "ml-auto bg-slate-900 text-white"
                        : "bg-white text-slate-900 shadow-sm"
                    }`}
                  >
                    {message.content}
                  </div>
                ))}
                {streamingReply !== null && (
                  <div className="max-w-2xl whitespace-pre-wrap rounded-lg bg-white px-4 py-2 text-sm text-slate-900 shadow-sm">
                    {streamingReply}
                    <span className="ml-0.5 animate-pulse text-slate-400">▍</span>
                  </div>
                )}
              </div>
              <div ref={messagesEndRef} />
            </div>

            <form onSubmit={handleSendMessage} className="border-t border-slate-200 p-4">
              <label className="mb-2 flex items-center gap-2 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={chatScopeOnly}
                  onChange={(e) => setChatScopeOnly(e.target.checked)}
                  disabled={chatInputDisabled}
                  className="h-3.5 w-3.5 rounded border-slate-300 disabled:cursor-not-allowed"
                />
                Only search this chat's documents
                <span className="text-slate-400">
                  (unchecked: searches every document you've uploaded across all your chats)
                </span>
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={messageInput}
                  onChange={(e) => setMessageInput(e.target.value)}
                  disabled={chatInputDisabled}
                  placeholder={
                    azureConfigured
                      ? "Ask a question about your uploaded documents..."
                      : "Configuration missing - set Azure OpenAI credentials in .env"
                  }
                  className="flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                />
                <button
                  type="submit"
                  disabled={chatInputDisabled || !messageInput.trim()}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
                >
                  {sending ? "Sending..." : "Send"}
                </button>
              </div>
            </form>
          </>
        )}
      </main>
    </div>
  );
}
