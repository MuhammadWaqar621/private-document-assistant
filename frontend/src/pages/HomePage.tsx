import { Link } from "react-router-dom";

import { useConfigStatus } from "../lib/useConfigStatus";

function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div
      className={`flex items-center justify-between rounded-lg border px-4 py-2 text-sm ${
        ok
          ? "border-green-200 bg-green-50 text-green-800"
          : "border-amber-200 bg-amber-50 text-amber-800"
      }`}
    >
      <span className="font-medium">{label}</span>
      <span>{ok ? "configured" : "missing"}</span>
    </div>
  );
}

export default function HomePage() {
  const { status, error } = useConfigStatus();

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-6 py-16">
        <div>
          <h1 className="text-4xl font-bold tracking-tight">querynest</h1>
          <p className="mt-2 text-slate-600">
            A RAG-powered document chat assistant. Upload documents, then ask
            questions grounded in their content.
          </p>
        </div>

        <div className="flex gap-3">
          <Link
            to="/login"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700"
          >
            Log in
          </Link>
          <Link
            to="/signup"
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
          >
            Sign up
          </Link>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold">Backend configuration</h2>

          {error && (
            <p className="text-sm text-red-600">
              Could not reach the backend ({error}). Is it running?
            </p>
          )}

          {!error && !status && (
            <p className="text-sm text-slate-500">Checking configuration...</p>
          )}

          {status && (
            <div className="flex flex-col gap-2">
              <StatusPill label="Azure OpenAI" ok={status.azure_ai} />
              <StatusPill label="Google OAuth" ok={status.google_oauth} />
              <StatusPill label="SMTP (email)" ok={status.smtp} />
            </div>
          )}

          <p className="mt-4 text-xs text-slate-400">
            Authentication, chat history, document ingestion, and the RAG
            chat pipeline all work end-to-end. A group above shows
            "missing" only if it depends on secrets this deployment hasn't
            set - sign-up/login/chat/documents work regardless.
          </p>
        </div>
      </div>
    </div>
  );
}
