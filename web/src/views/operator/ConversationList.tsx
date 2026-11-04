import type { ConversationSummary } from "../../api/types";
import { AgentBadge, Card, Empty, ErrorNote, Pill, Spinner } from "../../components/ui";
import { timeOnly } from "../../lib/format";
import type { AsyncState } from "../../lib/useAsync";

interface Props {
  state: AsyncState<ConversationSummary[]>;
  selected: string | null;
  onSelect: (id: string) => void;
}

export function ConversationList({ state, selected, onSelect }: Props) {
  const { data, error, loading, reload } = state;
  const awaiting = data?.filter((c) => c.status === "awaiting_approval").length ?? 0;

  return (
    <Card
      title="Live conversations"
      actions={
        data && (
          <span className="text-xs text-slate-500">
            {data.length} open{awaiting > 0 && <>, {awaiting} awaiting approval</>}
          </span>
        )
      }
    >
      {error && <ErrorNote message={error} onRetry={reload} />}
      {!data && loading && <Spinner label="Loading conversations" />}
      {data && data.length === 0 && <Empty>No conversations yet. Start one from the customer chat.</Empty>}
      {data && data.length > 0 && (
        <ul className="max-h-[26rem] divide-y divide-slate-100 overflow-y-auto">
          {data.map((c) => (
            <li key={c.conversation_id}>
              <button
                type="button"
                onClick={() => onSelect(c.conversation_id)}
                aria-current={selected === c.conversation_id}
                className={`flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors hover:bg-slate-50 ${
                  selected === c.conversation_id ? "bg-brand-50/70 shadow-[inset_3px_0_0_var(--color-brand-600)]" : ""
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-slate-700">{c.conversation_id}</span>
                  {c.status === "awaiting_approval" ? <Pill tone="warn">Awaiting approval</Pill> : <Pill tone="good">Active</Pill>}
                  <AgentBadge agent={c.agent} />
                  <span className="ml-auto text-xs text-slate-400">{timeOnly(c.updated_at)}</span>
                </div>
                <p className="line-clamp-2 text-sm text-slate-600">{c.last_message || "No messages"}</p>
                <span className="text-xs text-slate-400">{c.message_count} messages</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
