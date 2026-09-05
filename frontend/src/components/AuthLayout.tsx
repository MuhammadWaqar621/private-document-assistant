import type { ReactNode } from "react";
import { Link } from "react-router-dom";

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
        <Link to="/" className="mb-6 block text-center text-xl font-bold tracking-tight">
          querynest
        </Link>
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
      </div>
    </div>
  );
}
