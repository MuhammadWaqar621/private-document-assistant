import { useCallback, useRef, useState } from "react";
import { Paperclip, X } from "lucide-react";

import { ApiError, api } from "../lib/api";
import type { DocumentOut } from "../lib/types";

type Props = {
  chatId: number;
  documents: DocumentOut[];
  onUploaded: (doc: DocumentOut) => void;
  onAuthFailure: () => void;
  disabled?: boolean;
};

const statusStyles: Record<DocumentOut["status"], string> = {
  processing: "bg-amber-50 text-amber-700",
  ready: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
};

/**
 * Compact upload control meant to live inline in the chat input bar (an
 * attach icon, not a standalone drop-zone) - clicking it opens a file
 * picker for POST /api/chats/{chatId}/documents. Ingestion runs
 * synchronously on the backend, so the response already carries the final
 * status (ready/failed); uploaded files render as small chips above the
 * input rather than a separate panel elsewhere on the page.
 */
export default function DocumentUpload({
  chatId,
  documents,
  onUploaded,
  onAuthFailure,
  disabled,
}: Props) {
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
    <div className="flex flex-col gap-2">
      {documents.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {documents.map((doc) => (
            <li
              key={doc.id}
              title={doc.error_message ?? undefined}
              className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs shadow-sm"
            >
              <span className="max-w-[10rem] truncate text-slate-700">{doc.filename}</span>
              <span
                className={`shrink-0 rounded-full px-1.5 py-0.5 font-medium ${statusStyles[doc.status]}`}
              >
                {doc.status}
              </span>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p className="flex items-center gap-1 text-xs text-red-600">
          <X size={12} /> {error}
        </p>
      )}

      <button
        type="button"
        onClick={() => !disabled && !uploading && inputRef.current?.click()}
        disabled={disabled || uploading}
        title="Attach a .pdf/.docx/.txt/.jpg/.png document to this chat"
        className="flex w-fit items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Paperclip size={13} />
        {uploading ? "Uploading..." : "Attach document"}
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
    </div>
  );
}
