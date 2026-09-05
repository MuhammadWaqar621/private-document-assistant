import { useEffect, useState } from "react";

import { api } from "./api";
import type { ConfigStatus } from "./types";

/**
 * Fetches GET /api/config/status once. Used to hide/disable features that
 * depend on backend config which may not be set (Google OAuth button,
 * SMTP-dependent messaging).
 */
export function useConfigStatus(): { status: ConfigStatus | null; error: string | null } {
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    api
      .get<ConfigStatus>("/api/config/status")
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return { status, error };
}
