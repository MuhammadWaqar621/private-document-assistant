import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, Loader2, MessageSquarePlus, Mic, Send, Square, Trash2, Volume2 } from "lucide-react";

import DocumentUpload from "../components/DocumentUpload";
import { ApiError, api } from "../lib/api";
import { clearTokens } from "../lib/auth";
import { streamChatMessage } from "../lib/chatStream";
import { useConfigStatus } from "../lib/useConfigStatus";
import type { Chat, ChatDetail, ChatMessage, CurrentUser, DocumentOut } from "../lib/types";

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

  // --- Speech: mic-to-transcribe + per-message text-to-speech -----------
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  // Which message (by id) is currently being synthesized/played, so each
  // message's speaker button can show its own loading/playing state.
  const [synthesizingId, setSynthesizingId] = useState<number | null>(null);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);

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
    // A chat's title only ever changes once its first message is sent
    // (see the backend's auto-titling in app/api/messages.py) - so a chat
    // still titled "New chat" is guaranteed to have zero messages yet.
    // Reuse that one instead of creating another empty chat on top of it.
    const existingEmptyChat = chats.find((c) => c.title === "New chat");
    if (existingEmptyChat) {
      await selectChat(existingEmptyChat.id);
      return;
    }

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
        // The backend auto-titles a chat from its first message - keep the
        // sidebar list (separate state from the selected chat's detail) in
        // sync so the new title shows up there too, not just in the header.
        setChats((prev) =>
          prev.map((c) => (c.id === chatId ? { ...c, title: detail.title } : c)),
        );
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

  // Click once to start recording (browser MediaRecorder API), click again
  // to stop - the recorded blob is then POSTed to /api/speech/transcribe
  // and the returned text is appended to (or replaces, if empty) the
  // message input.
  async function toggleRecording() {
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      audioChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);

        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        if (blob.size === 0) return;

        setTranscribing(true);
        try {
          const formData = new FormData();
          formData.append("file", blob, "recording.webm");
          const result = await api.uploadAudio<{ text: string }>(
            "/api/speech/transcribe",
            formData,
          );
          setMessageInput((prev) => (prev.trim() ? `${prev.trim()} ${result.text}` : result.text));
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            handleAuthFailure();
            return;
          }
          setError(err instanceof Error ? err.message : "Transcription failed.");
        } finally {
          setTranscribing(false);
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setError("Could not access the microphone - check your browser's permissions.");
    }
  }

  // Speaker button next to an assistant message: POSTs the message's full
  // text to /api/speech/synthesize and plays the returned MP3 via a plain
  // Audio element. Clicking the currently-playing message's button again
  // stops it.
  async function playMessageAudio(message: ChatMessage) {
    if (playingId === message.id) {
      audioElementRef.current?.pause();
      setPlayingId(null);
      return;
    }

    audioElementRef.current?.pause();
    setSynthesizingId(message.id);
    try {
      const blob = await api.synthesizeSpeech(message.content);
      const audio = new Audio(URL.createObjectURL(blob));
      audioElementRef.current = audio;
      audio.onended = () => setPlayingId(null);
      setPlayingId(message.id);
      await audio.play();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Could not play audio.");
      setPlayingId(null);
    } finally {
      setSynthesizingId(null);
    }
  }

  const ragConfigured = configStatus?.rag ?? true; // avoid a flash of "disabled" while loading
  const speechConfigured = configStatus?.speech ?? true;
  const chatInputDisabled = !selectedChat || sending || !ragConfigured;

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      <aside className="flex w-72 flex-col border-r border-slate-200 bg-white">
        <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-4">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Lock size={14} strokeWidth={2.5} />
          </div>
          <span className="text-lg font-bold tracking-tight">QueryNest</span>
        </div>

        <div className="p-3">
          <button
            type="button"
            onClick={createChat}
            disabled={creating}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white shadow-card transition hover:bg-brand-700 disabled:opacity-50"
          >
            <MessageSquarePlus size={16} />
            {creating ? "Creating..." : "New chat"}
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
                  ? "bg-brand-50 font-medium text-brand-800"
                  : "text-slate-700 hover:bg-slate-50"
              }`}
            >
              <span className="truncate">{chat.title}</span>
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => deleteChat(chat.id, e)}
                className="ml-2 hidden shrink-0 text-slate-400 hover:text-red-600 group-hover:inline"
              >
                <Trash2 size={14} />
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
        {configStatus && !configStatus.rag && (
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
            Configuration missing - set Azure OpenAI embeddings credentials
            (AZURE_EM_*) and{" "}
            {configStatus.llm_provider === "azure" ? "Azure OpenAI chat" : "Groq chat"}{" "}
            credentials ({configStatus.llm_provider === "azure" ? "LLM_ENDPOINT*" : "GROQ_API_KEY"})
            in .env to enable document upload and chat.
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

            <div className="flex-1 overflow-y-auto px-6 py-4">
              {loadingMessages && <p className="text-sm text-slate-400">Loading messages...</p>}
              {!loadingMessages && selectedChat.messages.length === 0 && streamingReply === null && (
                <p className="text-sm text-slate-400">
                  No messages yet in this chat. Attach a document below, then
                  ask a question about it.
                </p>
              )}
              <div className="flex flex-col gap-3">
                {selectedChat.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`flex max-w-2xl items-end gap-1.5 ${
                      message.role === "user" ? "ml-auto flex-row-reverse" : ""
                    }`}
                  >
                    <div
                      className={`whitespace-pre-wrap rounded-lg px-4 py-2 text-sm ${
                        message.role === "user"
                          ? "bg-brand-600 text-white"
                          : "bg-white text-slate-900 shadow-card"
                      }`}
                    >
                      {message.content}
                    </div>
                    {message.role === "assistant" && speechConfigured && (
                      <button
                        type="button"
                        onClick={() => playMessageAudio(message)}
                        title={playingId === message.id ? "Stop" : "Read this reply aloud"}
                        className={`mb-1 shrink-0 rounded-full p-1.5 transition ${
                          playingId === message.id
                            ? "bg-brand-100 text-brand-700"
                            : "text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                        }`}
                      >
                        {synthesizingId === message.id ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : playingId === message.id ? (
                          <Square size={13} />
                        ) : (
                          <Volume2 size={13} />
                        )}
                      </button>
                    )}
                  </div>
                ))}
                {streamingReply !== null && (
                  <div className="max-w-2xl whitespace-pre-wrap rounded-lg bg-white px-4 py-2 text-sm text-slate-900 shadow-card">
                    {streamingReply}
                    <span className="ml-0.5 animate-pulse text-brand-500">▍</span>
                  </div>
                )}
              </div>
              <div ref={messagesEndRef} />
            </div>

            <form onSubmit={handleSendMessage} className="border-t border-slate-200 p-4">
              <div className="mb-2">
                <DocumentUpload
                  chatId={selectedChat.id}
                  documents={documents}
                  onUploaded={(doc) => setDocuments((prev) => [doc, ...prev])}
                  onAuthFailure={handleAuthFailure}
                  disabled={!ragConfigured}
                />
              </div>

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
                    ragConfigured
                      ? "Ask a question about your uploaded documents..."
                      : "Configuration missing - set AI credentials in .env"
                  }
                  className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm transition focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                />
                {speechConfigured && (
                  <button
                    type="button"
                    onClick={toggleRecording}
                    disabled={chatInputDisabled || transcribing}
                    title={recording ? "Stop recording" : "Record a voice message"}
                    className={`flex items-center justify-center rounded-lg border px-3 py-2 text-sm font-medium shadow-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${
                      recording
                        ? "border-red-300 bg-red-50 text-red-600 hover:bg-red-100"
                        : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {transcribing ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : recording ? (
                      <Square size={15} />
                    ) : (
                      <Mic size={15} />
                    )}
                  </button>
                )}
                <button
                  type="submit"
                  disabled={chatInputDisabled || !messageInput.trim()}
                  className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-card transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400"
                >
                  <Send size={15} />
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
