import { api } from "../../api/client";
import { useAsync } from "../../lib/useAsync";

/** Warns when the dashboard is showing the bundled sample instead of real eval results. */
export function SampleDataNote() {
  const { data: source } = useAsync(() => api.evalDataSource(), []);
  if (source !== "sample") return null;
  return (
    <div role="note" className="rounded-lg border border-amber-300 bg-amber-soft px-4 py-3 text-sm text-amber-ink">
      <span className="font-semibold">Sample data.</span> These numbers come from the bundled sample runs, not from the latest
      published eval results.
    </div>
  );
}
