import { Suspense } from "react";
import { NavLink, Outlet } from "react-router";
import { Spinner } from "./ui";
import { MOCK_MODE, STATIC_MODE } from "../lib/env";

const NAV = STATIC_MODE
  ? [{ to: "/evals", label: "Eval dashboard" }]
  : [
      { to: "/", label: "Customer chat", end: true },
      { to: "/operator", label: "Operator console" },
      { to: "/evals", label: "Eval dashboard" },
    ];

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-8 shrink-0" aria-hidden>
      <rect width="32" height="32" rx="7" fill="#f5a623" />
      <path d="M18 4 8 18h7l-2 10 11-15h-7z" fill="#0c2747" />
    </svg>
  );
}

export function Layout() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="bg-brand-900 text-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <Logo />
            <div className="min-w-0 leading-tight">
              <div className="font-semibold tracking-tight">GridDesk</div>
              <div className="truncate text-xs text-brand-200">Northbrook Power &amp; Light</div>
            </div>
          </div>
          <nav aria-label="Main" className="-mx-1 flex flex-wrap gap-1 sm:ml-auto">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={"end" in n ? n.end : false}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive ? "bg-white/15 text-white shadow-[inset_0_-2px_0_var(--color-amber-accent)]" : "text-brand-100 hover:bg-white/10 hover:text-white"
                  }`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      {STATIC_MODE && (
        <div className="border-b border-amber-200 bg-amber-soft px-4 py-2 text-center text-xs text-amber-ink">
          Static snapshot of the eval dashboard. The customer chat and operator console need the backend: run GridDesk locally to try them.
        </div>
      )}
      {MOCK_MODE && (
        <div className="border-b border-amber-200 bg-amber-soft px-4 py-2 text-center text-xs text-amber-ink">
          Mock mode: all data is simulated in the browser.
        </div>
      )}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Suspense fallback={<Spinner />}>
          <Outlet />
        </Suspense>
      </main>
      <footer className="border-t border-slate-200 bg-white px-4 py-3 text-center text-xs text-slate-500">
        GridDesk is a portfolio project. Northbrook Power &amp; Light and all customers are fictional.
      </footer>
    </div>
  );
}
