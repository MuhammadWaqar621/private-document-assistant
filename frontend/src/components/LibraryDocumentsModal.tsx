import { useCallback, useEffect, useRef, useState } from "react";
import { Library, Loader2, Paperclip, Trash2, X } from "lucide-react";

import { ApiError, api } from "../lib/api";
import type { DocumentOut } from "../lib/types";

type Props = {
  open: boolean;
  onClose: () => void;
  onAuthFailure: () => void;
};

const statusStyles: Record<DocumentOut["status"], string> = {
  processing: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  ready: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
};

/**
 * Account-level document library (POST/GET/DELETE /api/documents - no
 * chat_id involved, see backend/app/api/documents.py's library_router).
 * Anything uploaded here is automatically searchable from every chat this
 * user owns (the default scope="all" retrieval), without attaching it to
 * a chat first - "set up your personal chatbot once" instead of
 * re-uploading the same reference documents into each new chat.
 */
export default function LibraryDocumentsModal({ open, onClose, onAuthFailure }: Props) {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const docs = await api.get<DocumentOut[]>("/api/documents", true);
      setDocuments(docs);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onAuthFailure();
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load your documents.");
    } finally {
      setLoading(false);
    }
  }, [onAuthFailure]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const uploadFile = useCallback(
    async (file: File) => {
      setUploading(true);
      setError(null);
      try {
        const formData = new FormData();
        formData.append("file", file);
        const doc = await api.uploadFile<DocumentOut>("/api/documents", formData);
        setDocuments((prev) => [doc, ...prev]);
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
    [onAuthFailure],
  );

  const deleteDoc = useCallback(
    async (id: number) => {
      try {
        await api.del(`/api/documents/${id}`, true);
        setDocuments((prev) => prev.filter((d) => d.id !== id));
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to delete document.");
      }
    },
    [onAuthFailure],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-lg dark:bg-slate-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 dark:border-slate-800">
          <div className="flex items-center gap-2">
            <Library size={18} className="text-brand-600 dark:text-brand-400" />
            <h2 className="font-semibold dark:text-white">My documents</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-300"
          >
            <X size={18} />
          </button>
        </div>

        <p className="border-b border-slate-200 px-5 py-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          Documents you upload here are private to your account and are automatically
          included when you ask a question in any chat - no need to re-upload the same
          files into every new chat. Uncheck "Only search this chat's documents" in a
          chat if you want it to reach these too (it's unchecked by default).
        </p>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && <p className="text-sm text-slate-400 dark:text-slate-500">Loading...</p>}
          {!loading && documents.length === 0 && (
            <p className="text-sm text-slate-400 dark:text-slate-500">
              No documents yet - attach one below to get started.
            </p>
          )}
          {!loading && documents.length > 0 && (
            <ul className="flex flex-col gap-2">
              {documents.map((doc) => (
                <li
                  key={doc.id}
                  title={doc.error_message ?? undefined}
                  className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-700"
                >
                  <span className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-300">
                    {doc.filename}
                  </span>
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${statusStyles[doc.status]}`}
                  >
                    {doc.status}
                  </span>
                  <button
                    type="button"
                    onClick={() => deleteDoc(doc.id)}
                    title="Delete"
                    className="shrink-0 rounded-full p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <p className="border-t border-slate-200 px-5 py-2 text-xs text-red-600 dark:border-slate-800 dark:text-red-400">
            {error}
          </p>
        )}

        <div className="border-t border-slate-200 px-5 py-4 dark:border-slate-800">
          <button
            type="button"
            onClick={() => !uploading && inputRef.current?.click()}
            disabled={uploading}
            className="flex w-fit items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {uploading ? <Loader2 size={13} className="animate-spin" /> : <Paperclip size={13} />}
            {uploading ? "Uploading..." : "Upload a document"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,.txt,.jpg,.jpeg,.png"
            disabled={uploading}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadFile(file);
              e.target.value = "";
            }}
          />
        </div>
      </div>
    </div>
  );
}
