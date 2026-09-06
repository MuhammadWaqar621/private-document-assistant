import { useCallback, useRef, useState } from "react";
import { Paperclip } from "lucide-react";

import { ApiError, api } from "../lib/api";
import type { DocumentOut } from "../lib/types";

type Props = {
  chatId: number;
  onUploaded: (doc: DocumentOut) => void;
  onAuthFailure: () => void;
  disabled?: boolean;
};

/**
 * Icon-only attach control meant to sit inline in the chat composer's
 * single input bar, alongside the mic and send buttons (see
 * AppShellPage.tsx) - clicking it opens a file picker for
 * POST /api/chats/{chatId}/documents. Ingestion runs synchronously on the
 * backend, so the response already carries the final status
 * (ready/failed); the uploaded-file chips themselves are rendered by the
 * parent from its own `documents` state, not by this component.
 */
export default function DocumentUpload({ chatId, onUploaded, onAuthFailure, disabled }: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadFile = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      try {
        const formData = new FormData();
        formData.append("file", file);
        const doc = await api.uploadFile<DocumentOut>(
          `/api/chats/${chatId}/documents`,
          formData,
        );
        onUploaded(doc);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "Upload failed.");
      } finally {
        setUploading(false);
      }
    },
    [chatId, onUploaded, onAuthFailure],
  );

  return (
    <div className="relative flex shrink-0 items-center">
      <button
        type="button"
        onClick={() => !disabled && !uploading && inputRef.current?.click()}
        disabled={disabled || uploading}
        title="Attach a .pdf/.docx/.txt/.jpg/.png document to this chat"
        className="flex h-9 w-9 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        <Paperclip size={17} />
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.txt,.jpg,.jpeg,.png"
        disabled={disabled || uploading}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) uploadFile(file);
          e.target.value = "";
        }}
      />
      {error && (
        <p className="absolute bottom-full left-0 mb-1 w-max max-w-[16rem] rounded-md bg-red-600 px-2 py-1 text-xs text-white shadow-lg">
          {error}
        </p>
      )}
    </div>
  );
}
