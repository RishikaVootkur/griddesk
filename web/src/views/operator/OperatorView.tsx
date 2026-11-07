import { useState } from "react";
import { api } from "../../api/client";
import { Button } from "../../components/ui";
import { operatorToken, setOperatorToken } from "../../lib/operatorToken";
import { useAsync } from "../../lib/useAsync";
import { ApprovalQueue } from "./ApprovalQueue";
import { AuditLog } from "./AuditLog";
import { ConversationList } from "./ConversationList";
import { ConversationPanel } from "./ConversationPanel";

const POLL_MS = 5000;

export function OperatorView() {
  // Bumped when the operator token changes, so every panel reloads with the new credentials.
  const [session, setSession] = useState(0);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Operator console</h1>
          <p className="text-sm text-slate-500">Live conversations, supervisor approvals and the policy audit trail. Refreshes every 5 seconds.</p>
        </div>
        <OperatorTokenForm onChange={() => setSession((s) => s + 1)} />
      </div>
      <OperatorPanels key={session} />
    </div>
  );
}

function OperatorTokenForm({ onChange }: { onChange: () => void }) {
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(() => operatorToken() !== "");

  return (
    <form
      className="flex items-center gap-2 text-xs text-slate-500"
      onSubmit={(e) => {
        e.preventDefault();
        setOperatorToken(value.trim());
        setSaved(value.trim() !== "");
        setValue("");
        onChange();
      }}
    >
      <label className="flex items-center gap-2" title="The OPERATOR_TOKEN the API was started with. Kept for this browser session only.">
        Operator token
        <input
          type="password"
          value={value}
          autoComplete="off"
          placeholder={saved ? "saved for this session" : "paste token"}
          onChange={(e) => setValue(e.target.value)}
          className="w-44 rounded-md border border-slate-300 px-2 py-1 text-sm text-slate-800 focus:border-brand-500 focus:outline-none"
        />
      </label>
      <Button variant="secondary" type="submit">
        {value ? "Sign in" : saved ? "Sign out" : "Sign in"}
      </Button>
    </form>
  );
}

function OperatorPanels() {
  const [selected, setSelected] = useState<string | null>(null);
  const conversations = useAsync(() => api.listConversations(), [], POLL_MS);
  const approvals = useAsync(() => api.listApprovals(), [], POLL_MS);

  function onDecided() {
    approvals.reload();
    conversations.reload();
  }

  return (
    <>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <ConversationList state={conversations} selected={selected} onSelect={setSelected} />
        <ConversationPanel conversationId={selected} onClose={() => setSelected(null)} refreshKey={conversations.data} />
      </div>

      <ApprovalQueue state={approvals} onDecided={onDecided} />
      <AuditLog />
    </>
  );
}
