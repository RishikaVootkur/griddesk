import { useState } from "react";
import { api } from "../../api/client";
import { useAsync } from "../../lib/useAsync";
import { ApprovalQueue } from "./ApprovalQueue";
import { AuditLog } from "./AuditLog";
import { ConversationList } from "./ConversationList";
import { ConversationPanel } from "./ConversationPanel";

const POLL_MS = 5000;

export function OperatorView() {
  const [selected, setSelected] = useState<string | null>(null);
  const conversations = useAsync(() => api.listConversations(), [], POLL_MS);
  const approvals = useAsync(() => api.listApprovals(), [], POLL_MS);

  function onDecided() {
    approvals.reload();
    conversations.reload();
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Operator console</h1>
        <p className="text-sm text-slate-500">Live conversations, supervisor approvals and the policy audit trail. Refreshes every 5 seconds.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <ConversationList state={conversations} selected={selected} onSelect={setSelected} />
        <ConversationPanel conversationId={selected} onClose={() => setSelected(null)} refreshKey={conversations.data} />
      </div>

      <ApprovalQueue state={approvals} onDecided={onDecided} />
      <AuditLog />
    </div>
  );
}
