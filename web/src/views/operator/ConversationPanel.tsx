import { api } from "../../api/client";
import { Transcript } from "../../components/Transcript";
import { Button, Card, Empty, ErrorNote, Pill, Spinner } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";

interface Props {
  conversationId: string | null;
  onClose: () => void;
  /** Changes whenever the conversation list refreshes, so the open transcript stays current. */
  refreshKey: unknown;
}

export function ConversationPanel({ conversationId, onClose, refreshKey }: Props) {
  const { data, error, loading, reload } = useAsync(
    () => (conversationId ? api.getConversation(conversationId) : Promise.resolve(null)),
    [conversationId, refreshKey],
  );
  const detail = data && data.conversation_id === conversationId ? data : null;

  return (
    <Card
      title={conversationId ? <span className="font-mono">{conversationId}</span> : "Transcript"}
      actions={
        conversationId && (
          <>
            {detail?.awaiting_approval && <Pill tone="warn">Awaiting: {detail.awaiting_approval.action}</Pill>}
            <Pill>Read only</Pill>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </>
        )
      }
    >
      {!conversationId && <Empty>Select a conversation to read its transcript.</Empty>}
      {conversationId && error && <ErrorNote message={error} onRetry={reload} />}
      {conversationId && !detail && loading && <Spinner label="Loading transcript" />}
      {detail && (
        <div className="max-h-[26rem] overflow-y-auto bg-slate-50/60 px-3 py-4 sm:px-4">
          {detail.messages.length === 0 ? <Empty>No messages yet.</Empty> : <Transcript lines={detail.messages} />}
        </div>
      )}
    </Card>
  );
}
