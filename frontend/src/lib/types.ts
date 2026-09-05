export type ConfigStatus = {
  azure_ai: boolean;
  smtp: boolean;
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
