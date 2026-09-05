import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Lock } from "lucide-react";

export default function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-16 text-slate-900">
      <div className="w-full max-w-sm">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Lock size={16} strokeWidth={2.5} />
          </div>
          <span className="text-xl font-bold tracking-tight">QueryNest</span>
        </Link>
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <h1 className="text-lg font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}
