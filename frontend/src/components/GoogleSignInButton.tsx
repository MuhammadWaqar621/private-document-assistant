import { apiUrl } from "../lib/config";
import { useConfigStatus } from "../lib/useConfigStatus";

/**
 * "Sign in with Google" button. Renders as a real link to
 * GET /api/auth/google/login (a full-page redirect, not a fetch) only
 * once /api/config/status reports google_oauth: true; otherwise renders a
 * disabled, greyed-out button with a tooltip rather than a dead link.
 */
export default function GoogleSignInButton() {
  const { status } = useConfigStatus();

  if (status === null) {
    return null; // still loading - avoid a flash of a disabled button
  }

  if (!status.google_oauth) {
    return (
      <button
        type="button"
        disabled
        title="Google sign-in is not configured on this server."
        className="w-full cursor-not-allowed rounded-lg border border-slate-200 bg-slate-100 px-4 py-2 text-sm font-medium text-slate-400"
      >
        Sign in with Google (not configured)
      </button>
    );
  }

  return (
    <a
      href={apiUrl("/api/auth/google/login")}
      className="flex w-full items-center justify-center rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
    >
      Sign in with Google
    </a>
  );
}
