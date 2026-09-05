export type ConfigStatus = {
  // Document upload + chat: embeddings (always Azure OpenAI) AND whichever
  // chat provider `llm_provider` names below are fully configured.
  rag: boolean;
  smtp: boolean;
  // Speech-to-text (mic) + text-to-speech (per-message speaker button) -
  // both backed by Groq, gated on GROQ_API_KEY alone.
  speech: boolean;
  // Which provider is currently serving chat completions - "groq"
  // (default) or "azure". Embeddings are always Azure regardless.
  llm_provider: "groq" | "azure";
};

export type CurrentUser = {
  id: number;
  email: string;
};

export type Chat = {
  id: number;
  title: string;
  created_at: string;
};

export type MessageRole = "user" | "assistant";

export type ChatMessage = {
  id: number;
  role: MessageRole;
  content: string;
  created_at: string;
};

export type ChatDetail = Chat & {
  messages: ChatMessage[];
};

export type DocumentStatus = "processing" | "ready" | "failed";

export type DocumentOut = {
  id: number;
  filename: string;
  status: DocumentStatus;
  error_message: string | null;
  created_at: string;
};
