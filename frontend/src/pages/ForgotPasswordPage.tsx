import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import AuthLayout from "../components/AuthLayout";
import { ApiError, api } from "../lib/api";

const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500";

function isSmtpNotConfigured(err: ApiError): boolean {
  const detail = (err.body as { detail?: { error?: string } } | null)?.detail;
  return detail?.error === "smtp_not_configured";
}

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      const res = await api.post<{ message: string }>("/api/auth/forgot-password", { email });
      setMessage(res.message);
    } catch (err) {
      if (err instanceof ApiError && isSmtpNotConfigured(err)) {
        setError("Email sending is not configured - configuration missing.");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Forgot your password?"
      subtitle="Enter your email and we'll send you a reset link."
    >
      {message ? (
        <p className="rounded-lg bg-green-50 px-3 py-2 text-sm text-green-800">{message}</p>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputClass}
            />
          </div>

          {error && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">{error}</p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-1 w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-50"
          >
            {submitting ? "Sending..." : "Send reset link"}
          </button>
        </form>
      )}

      <p className="mt-5 text-center text-sm text-slate-500">
        <Link to="/login" className="font-medium text-slate-900 hover:underline">
          Back to log in
        </Link>
      </p>
    </AuthLayout>
  );
}
