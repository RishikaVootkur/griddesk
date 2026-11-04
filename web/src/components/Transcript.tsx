import { AgentBadge } from "./ui";

export interface TranscriptLine {
  role: "customer" | "agent" | "system";
  text: string;
  agent?: string | null;
}

export function Transcript({ lines, customerLabel = "Customer" }: { lines: TranscriptLine[]; customerLabel?: string }) {
  return (
    <ol className="flex flex-col gap-3">
      {lines.map((m, i) => (
        <li key={i} className="flex flex-col">
          <MessageBubble line={m} customerLabel={customerLabel} />
        </li>
      ))}
    </ol>
  );
}

export function MessageBubble({ line, customerLabel = "You" }: { line: TranscriptLine; customerLabel?: string }) {
  if (line.role === "system") {
    return (
      <p className="mx-auto max-w-[90%] rounded-md bg-slate-100 px-3 py-1 text-center text-xs text-slate-600">{line.text}</p>
    );
  }
  const isCustomer = line.role === "customer";
  return (
    <div className={`flex max-w-[88%] flex-col gap-1 sm:max-w-[75%] ${isCustomer ? "items-end self-end" : "items-start self-start"}`}>
      <div className="flex items-center gap-2 px-1 text-xs text-slate-500">
        {isCustomer ? customerLabel : line.agent ? <AgentBadge agent={line.agent} /> : "Agent"}
      </div>
      <div
        className={`whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
          isCustomer ? "rounded-br-sm bg-brand-700 text-white" : "rounded-bl-sm border border-slate-200 bg-white text-slate-800"
        }`}
      >
        {line.text}
      </div>
    </div>
  );
}
