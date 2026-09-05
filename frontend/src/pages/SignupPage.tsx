import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import AuthLayout from "../components/AuthLayout";
import GoogleSignInButton from "../components/GoogleSignInButton";
import { ApiError, api } from "../lib/api";
import { setTokens } from "../lib/auth";

type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

const inputClass =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500";

export default function SignupPage() {
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setSubmitting(true);
    try {
      const tokens = await api.post<TokenResponse>("/api/auth/signup", { email, password });
      setTokens(tokens.access_token, tokens.refresh_token);
      navigate("/app");
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

  return (
    <AuthLayout title="Create your account" subtitle="Start chatting with your documents.">
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

        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium text-slate-700">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        </div>

        <div>
          <label htmlFor="confirmPassword" className="mb-1 block text-sm font-medium text-slate-700">
            Confirm password
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
          {submitting ? "Creating account..." : "Sign up"}
        </button>
      </form>

      <div className="my-4 flex items-center gap-3 text-xs text-slate-400">
        <div className="h-px flex-1 bg-slate-200" />
        or
        <div className="h-px flex-1 bg-slate-200" />
      </div>

      <GoogleSignInButton />

      <p className="mt-5 text-center text-sm text-slate-500">
        Already have an account?{" "}
        <Link to="/login" className="font-medium text-slate-900 hover:underline">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
