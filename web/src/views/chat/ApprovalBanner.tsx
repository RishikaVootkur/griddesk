export function ApprovalBanner({ action }: { action: string }) {
  return (
    <div role="status" className="flex items-start gap-3 border-b border-amber-200 bg-amber-soft px-4 py-2.5 text-sm text-amber-ink">
      <svg viewBox="0 0 20 20" className="mt-0.5 size-4 shrink-0" fill="currentColor" aria-hidden>
        <path d="M10 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm.75 4a.75.75 0 0 0-1.5 0v4.25c0 .2.08.39.22.53l2.5 2.5a.75.75 0 1 0 1.06-1.06l-2.28-2.28V6Z" />
      </svg>
      <p className="min-w-0">
        <span className="font-semibold">Waiting for supervisor approval.</span>{" "}
        A supervisor is reviewing <code className="rounded bg-amber-100 px-1 font-mono text-xs break-words">{action}</code>. You will see the outcome here.
      </p>
    </div>
  );
}
