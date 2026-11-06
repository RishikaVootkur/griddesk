import { useEffect, useRef, useState } from "react";
import { api, describeError } from "../../api/client";
import type { ChatMessage, PendingApproval } from "../../api/types";
import { MessageBubble } from "../../components/Transcript";
import { AgentBadge, Button } from "../../components/ui";
import { ApprovalBanner } from "./ApprovalBanner";
import { Composer } from "./Composer";

const SUGGESTIONS = [
  "What is my current balance?",
  "Is there an outage on my street?",
  "I think I was overcharged, can I get a credit?",
];

export function ChatView() {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const lastAgent = [...messages].reverse().find((m) => m.role === "agent")?.agent ?? null;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streaming]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // While a supervisor decides, poll the conversation so the outcome shows up without a refresh.
  useEffect(() => {
    if (!pending || !conversationId || streaming) return;
    const id = setInterval(async () => {
      try {
        const detail = await api.getConversation(conversationId);
        if (!detail.awaiting_approval) {
          setMessages(detail.messages);
          setPending(null);
        }
      } catch {
        // transient; try again next tick
      }
    }, 4000);
    return () => clearInterval(id);
  }, [pending, conversationId, streaming]);

  async function send(text: string) {
    setError(null);
    setStreaming(true);
    setMessages((m) => [...m, { role: "customer", text, agent: null }]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      let id = conversationId;
      if (!id) {
        id = await api.createConversation(controller.signal);
        // "New conversation" may have been clicked while this was in flight.
        if (controller.signal.aborted) return;
        setConversationId(id);
      }
      for await (const ev of api.sendMessage(id, text, controller.signal)) {
        if (ev.type === "agent") setMessages((m) => [...m, { role: "agent", text: ev.text, agent: ev.agent }]);
        else if (ev.type === "approval") setPending({ approval_request_id: ev.approval_request_id, action: ev.action });
        else if (ev.type === "error") setError(ev.message);
      }
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setError(describeError(e));
    } finally {
      // A newer send may already own the stream state.
      if (abortRef.current === controller) setStreaming(false);
    }
  }

  function reset() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setConversationId(null);
    setMessages([]);
    setPending(null);
    setError(null);
  }

  return (
    <div className="mx-auto flex h-[calc(100dvh-11rem)] min-h-[28rem] max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-slate-900">Customer support chat</h1>
          <p className="truncate text-xs text-slate-500">
            {conversationId ? `Conversation ${conversationId}` : "Ask about bills, outages, or your account."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {lastAgent && (
            <span className="hidden items-center gap-1.5 text-xs text-slate-500 sm:flex">
              Talking to <AgentBadge agent={lastAgent} />
            </span>
          )}
          <Button variant="secondary" onClick={reset} disabled={messages.length === 0}>
            New conversation
          </Button>
        </div>
      </header>

      {pending && <ApprovalBanner action={pending.action} />}

      <div className="flex-1 overflow-y-auto bg-slate-50/60 px-3 py-4 sm:px-5" aria-live="polite">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <div>
              <p className="font-medium text-slate-800">Hi, welcome to Northbrook Power &amp; Light.</p>
              <p className="mt-1 text-sm text-slate-500">Our assistant can check balances, report outages, and update your account.</p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-full border border-brand-200 bg-white px-3 py-1.5 text-sm text-brand-700 hover:bg-brand-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="flex flex-col gap-3">
            {messages.map((m, i) => (
              <li key={i} className="flex flex-col">
                <MessageBubble line={m} />
              </li>
            ))}
            {streaming && (
              <li className="flex items-center gap-2 self-start px-1 text-xs text-slate-500">
                <span className="flex gap-1" aria-hidden>
                  <span className="size-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.2s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-brand-500 [animation-delay:-0.1s]" />
                  <span className="size-1.5 animate-bounce rounded-full bg-brand-500" />
                </span>
                Agent is working on it
              </li>
            )}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      {error && (
        <div role="alert" className="border-t border-red-100 bg-red-50 px-4 py-2 text-sm text-red-800">
          {error}
        </div>
      )}

      <Composer disabled={streaming} onSend={(t) => void send(t)} />
    </div>
  );
}
