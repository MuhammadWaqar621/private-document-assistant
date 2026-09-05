import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import AuthLayout from "../components/AuthLayout";
import { ApiError, api } from "../lib/api";

const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500";

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setMessage(null);

    if (newPassword !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await api.post<{ message: string }>("/api/auth/reset-password", {
        token,
        new_password: newPassword,
      });
      setMessage(res.message);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <AuthLayout title="Reset your password">
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          This link is missing a reset token. Request a new one from the{" "}
          <Link to="/forgot-password" className="font-medium underline">
            forgot password
          </Link>{" "}
          page.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Reset your password">
      {message ? (
        <div className="flex flex-col gap-3">
          <p className="rounded-lg bg-green-50 px-3 py-2 text-sm text-green-800">{message}</p>
          <Link
            to="/login"
            className="w-full rounded-lg bg-slate-900 px-4 py-2 text-center text-sm font-medium text-white shadow-sm transition hover:bg-slate-700"
          >
            Go to log in
          </Link>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div>
            <label htmlFor="newPassword" className="mb-1 block text-sm font-medium text-slate-700">
              New password
            </label>
            <input
              id="newPassword"
              type="password"
              required
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className={inputClass}
            />
          </div>

          <div>
            <label
              htmlFor="confirmPassword"
              className="mb-1 block text-sm font-medium text-slate-700"
            >
              Confirm new password
            </label>
            <input
              id="confirmPassword"
              type="password"
              required
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className={inputClass}
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="mt-1 w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-50"
          >
            {submitting ? "Resetting..." : "Reset password"}
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
