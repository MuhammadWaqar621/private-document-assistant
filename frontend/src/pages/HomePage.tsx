import { Link } from "react-router-dom";
import { FileText, Lock, MessageSquareText, ShieldCheck } from "lucide-react";

import { useConfigStatus } from "../lib/useConfigStatus";

function Logo() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
        <Lock size={16} strokeWidth={2.5} />
      </div>
      <span className="text-lg font-bold tracking-tight text-slate-900">QueryNest</span>
    </div>
  );
}

function StatusPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div
      className={`flex items-center justify-between rounded-lg border px-3 py-1.5 text-xs ${
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

const features = [
  {
    icon: ShieldCheck,
    title: "Private by design",
    description:
      "Every document is scoped to your account. General AI tools like ChatGPT or Claude have never seen it and never will.",
  },
  {
    icon: FileText,
    title: "Any document",
    description:
      "Upload PDFs, Word docs, or plain text. Each file is chunked, embedded, and indexed automatically for retrieval.",
  },
  {
    icon: MessageSquareText,
    title: "Real answers, live",
    description:
      "Responses stream in token-by-token and cite the exact page they came from - never a generic, unsourced reply.",
  },
];

export default function HomePage() {
  const { status, error } = useConfigStatus();

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Logo />
          <nav className="flex items-center gap-2">
            <Link
              to="/login"
              className="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 transition hover:text-slate-900"
            >
              Log in
            </Link>
            <Link
              to="/signup"
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white shadow-card transition hover:bg-brand-700"
            >
              Sign up
            </Link>
          </nav>
        </div>
      </header>

      <main className="flex-1">
        <section className="mx-auto max-w-3xl px-6 pb-16 pt-20 text-center">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700">
            <Lock size={12} /> Private &amp; secure by default
          </span>
          <h1 className="mt-6 text-4xl font-extrabold tracking-tight text-slate-900 sm:text-5xl">
            Your documents. Your AI.
            <br />
            <span className="text-brand-600">Completely private.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-lg text-slate-600">
            Upload files that are yours alone, and get answers grounded
            strictly in their content - never in public training data. No
            general-purpose model has ever seen what you upload here.
          </p>
          <div className="mt-8 flex items-center justify-center gap-3">
            <Link
              to="/signup"
              className="rounded-lg bg-brand-600 px-6 py-3 text-sm font-semibold text-white shadow-card transition hover:bg-brand-700"
            >
              Get started free
            </Link>
            <Link
              to="/login"
              className="rounded-lg border border-slate-300 bg-white px-6 py-3 text-sm font-semibold text-slate-700 shadow-card transition hover:bg-slate-50"
            >
              Log in
            </Link>
          </div>
        </section>

        <section className="mx-auto max-w-5xl px-6 pb-20">
          <div className="grid gap-6 sm:grid-cols-3">
            {features.map(({ icon: Icon, title, description }) => (
              <div
                key={title}
                className="rounded-xl border border-slate-200 bg-white p-6 shadow-card"
              >
                <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                  <Icon size={20} strokeWidth={2} />
                </div>
                <h3 className="font-semibold text-slate-900">{title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-slate-600">{description}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-5xl px-6 pb-20">
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Live demo status
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              This deployment's backend configuration, checked in real time.
              Everything else - sign-up, login, chat, and document upload -
              works regardless of what's shown below.
            </p>

            {error && (
              <p className="mt-4 text-sm text-red-600">
                Could not reach the backend ({error}). Is it running?
              </p>
            )}

            {!error && !status && (
              <p className="mt-4 text-sm text-slate-500">Checking configuration...</p>
            )}

            {status && (
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
                <StatusPill label="Azure OpenAI" ok={status.azure_ai} />
                <StatusPill label="SMTP (email)" ok={status.smtp} />
              </div>
            )}
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white px-6 py-6 text-center text-xs text-slate-400">
        QueryNest - a private, secure document chat assistant.
      </footer>
    </div>
  );
}
