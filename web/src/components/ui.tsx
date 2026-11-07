import type { ComponentProps, ReactNode } from "react";

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 px-4 py-6 text-sm text-slate-500">
      <span className="size-4 animate-spin rounded-full border-2 border-brand-200 border-t-brand-600" aria-hidden />
      {label}...
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="m-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="rounded-md px-2 py-1 font-medium text-red-800 hover:bg-red-100">
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="px-4 py-8 text-center text-sm text-slate-500">{children}</p>;
}

const AGENT_STYLES: Record<string, string> = {
  triage: "bg-slate-100 text-slate-700 ring-slate-200",
  billing: "bg-brand-50 text-brand-700 ring-brand-200",
  outages: "bg-amber-soft text-amber-ink ring-amber-200",
  account: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export function AgentBadge({ agent }: { agent: string | null | undefined }) {
  if (!agent) return null;
  const style = AGENT_STYLES[agent] ?? "bg-indigo-50 text-indigo-700 ring-indigo-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${style}`}>
      {agent} agent
    </span>
  );
}

type Tone = "neutral" | "good" | "bad" | "warn" | "info";
const TONES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700",
  good: "bg-emerald-50 text-emerald-800",
  bad: "bg-red-50 text-red-700",
  warn: "bg-amber-soft text-amber-ink",
  info: "bg-brand-50 text-brand-700",
};

export function Pill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>;
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ComponentProps<"button"> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-brand-700 text-white hover:bg-brand-800 disabled:bg-brand-300",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400",
    danger: "border border-red-200 bg-white text-red-700 hover:bg-red-50 disabled:text-red-300",
    ghost: "text-brand-700 hover:bg-brand-50 disabled:text-slate-400",
  }[variant];
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles} ${className}`}
    />
  );
}
