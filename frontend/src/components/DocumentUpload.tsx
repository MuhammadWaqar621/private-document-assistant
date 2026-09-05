import { useCallback, useRef, useState } from "react";
import type { DragEvent } from "react";

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
 * Drag-and-drop / click-to-browse upload widget for
 * POST /api/chats/{chatId}/documents. Ingestion runs synchronously on the
 * backend, so the response already carries the final status
 * (ready/failed) - there's no need to poll, but the per-document status
 * list re-renders from the parent's `documents` prop either way, which
 * would also pick up a later refetch/poll if that ever changes.
 */
export default function DocumentUpload({
  chatId,
  documents,
  onUploaded,
  onAuthFailure,
  disabled,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
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

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const file = event.dataTransfer.files?.[0];
    if (file) uploadFile(file);
  }

  return (
    <div className="border-b border-slate-200 p-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !disabled && !uploading && inputRef.current?.click()}
        className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-4 text-center text-sm transition ${
          disabled
            ? "cursor-not-allowed border-slate-200 bg-slate-50 text-slate-300"
            : dragOver
              ? "cursor-pointer border-slate-400 bg-slate-50 text-slate-600"
              : "cursor-pointer border-slate-200 text-slate-500 hover:border-slate-300 hover:bg-slate-50"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.txt"
          disabled={disabled || uploading}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) uploadFile(file);
            e.target.value = "";
          }}
        />
        {uploading ? "Uploading and processing..." : "Drag & drop a .pdf/.docx/.txt file, or click to browse"}
      </div>

      <p className="mt-2 text-xs text-slate-400">
        By default, questions in any of your chats can draw on documents
        uploaded here - use the{" "}
        <span className="font-medium text-slate-500">
          "Only search this chat's documents"
        </span>{" "}
        checkbox below the message box to restrict a question to just this
        chat's uploads. Either way, your documents are never visible to
        other users.
      </p>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {documents.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1">
          {documents.map((doc) => (
            <li
              key={doc.id}
              title={doc.error_message ?? undefined}
              className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2 py-1 text-xs"
            >
              <span className="truncate text-slate-700">{doc.filename}</span>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 font-medium ${statusStyles[doc.status]}`}
              >
                {doc.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
