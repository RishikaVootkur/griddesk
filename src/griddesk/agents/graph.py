"""LangGraph conversation graph.

    START -> agent --tool calls--> tools -> agent ...
                   --approval pending--> approval (interrupt; resumes on operator decision) -> agent
                   --plain reply that promises a transfer, no transfer call--> nudge -> agent (once a turn)
                   --plain reply that claims an action no tool took this turn--> claim_check -> agent
                     (the reply is held back from the customer; once a turn)
                   --plain reply--> END (wait for the customer's next message)

One graph invocation is one customer turn. State is checkpointed per conversation (`thread_id`), so
a conversation, including one paused for approval, survives a process restart when a durable
checkpointer is used. Agents are described by `AgentSpec`; with more than one spec, each agent gets
`transfer_to_<name>` handoff tools and the active agent is part of the shared state.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from griddesk.agents.claims import claims_action
from griddesk.agents.prompts import system_prompt
from griddesk.agents.toolbox import Toolbox, compact
from griddesk.gateway import BudgetExceeded, Gateway, LLMRequest, Message, ProviderError, ToolSpec
from griddesk.observability.tracing import set_attrs, span

HANDOFF_PREFIX = "transfer_to_"
RUNTIME_NOTICE = "RUNTIME NOTICE (from the GridDesk system, not the customer): "
_NOTICE_LOOKALIKE = re.compile(r"runtime\s*notice", re.IGNORECASE)
# "Let me connect you with our account specialist" said without calling transfer_to_account leaves the
# customer with nobody. Error analysis found this on paperless and hazard tasks in the multi-agent design.
# Only the front desk is checked, and only for a transfer verb aimed at the customer followed by a person
# or team, so "we will send you a paper bill for your account" never matches.
_HANDOFF_VERB = (
    r"\b(connect(ing)?|transfer(ring)?|hand(ing)?|pass(ing)?|rout(e|ing)|put(ting)?|get(ting)?)\s+you\b"
)
_PROMISED_HANDOFF = re.compile(
    # "connect you with our account specialist", "transferring you to the billing team"
    rf"{_HANDOFF_VERB}[^.!?]{{0,60}}\b(specialists?|team|department|desk|colleagues?|representative|agent)\b"
    # "transfer you to billing", "put you through to outages": the destination right after the verb
    rf"|{_HANDOFF_VERB}\s+(through\s+|over\s+)?(to|with)\s+(our\s+|the\s+)?(billing|outages?|account)\b"
    # "one moment while I transfer you."
    r"|\btransfer(ring)?\s+you\s*[.!]",
    re.IGNORECASE,
)
NUDGE_NOTE = (
    "You told the customer you would hand them to a specialist, but you did not call a transfer_to_* tool, "
    "so nobody has picked up the request. Call the right transfer_to_* tool now. Do not repeat your last "
    "message to the customer."
)


# Tools that only read. Any other tool that succeeded (or went to a supervisor) this turn counts as an action.
READ_ONLY_TOOLS = frozenset(
    {
        "explain_bill",
        "get_account_summary",
        "get_contact_info",
        "get_restoration_estimate",
        "list_bills",
        "lookup_address",
        "lookup_outage",
        "search_policy",
        "verify_identity",
    }
)
CLAIM_NOTE = (
    "Your last reply says an action was completed, but no tool call in this turn performed it, so that reply "
    "was not sent to the customer. Only say something was done if a tool result in this conversation shows "
    "it. If it was done earlier in this conversation, you may say so. Otherwise call the right tool, or tell "
    "the customer plainly what you can and cannot do. Write the reply the customer should see."
)
# Sent in place of a second unsupported claim in the same turn, only when nothing has been changed in the
# whole conversation, so the sentence is always true.
SAFE_REPLY = (
    "I need to correct myself: nothing has been changed on your account in this conversation yet. I can "
    "check what is possible for your account and explain the next step. What would you like me to do?"
)
SAFE_REPLY_PENDING = (
    "I need to correct myself: your request is waiting for a supervisor, and nothing has been changed on "
    "your account yet. You will see the outcome here."
)


def promises_handoff(text: str) -> bool:
    return bool(_PROMISED_HANDOFF.search(text or ""))


def is_internal_note(message: dict[str, Any]) -> bool:
    """Coaching notes (handoff nudge, claim check) and held-back replies are never shown to the customer."""
    return bool(message.get("internal"))


def neutralize_customer_text(text: str) -> str:
    """Customers cannot forge runtime notices: any look-alike in their text is defanged."""
    return _NOTICE_LOOKALIKE.sub("[customer-typed text]", text)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str  # prompt role
    tools: tuple[str, ...]
    description: str = ""


@dataclass
class AgentConfig:
    model: str
    today: date  # the simulated utility date; required so prompts match the tools and replays
    fallbacks: list[str] = field(default_factory=list)
    temperature: float = 0.0
    max_steps_per_turn: int = 8
    namespace: str = ""  # gateway cache namespace (the eval trial)


class ConvState(TypedDict, total=False):
    conversation_id: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    agent: str
    steps: int
    pending: list[dict[str, str]]  # approvals waiting for an operator, oldest first
    nudged: bool  # a promised-but-missing handoff was already flagged this turn
    handed_off: bool  # a transfer_to_* call ran this turn
    acted: bool  # a tool that changes something succeeded (or went to a supervisor) this turn
    claim_checked: bool  # a reply claiming an unperformed action was already held back this turn
    ever_acted: bool  # a tool that changes something succeeded at some point in this conversation
    error: str | None


def _handoff_spec(target: AgentSpec) -> ToolSpec:
    return ToolSpec(
        name=f"{HANDOFF_PREFIX}{target.name}",
        description=f"Hand the conversation to the {target.name} specialist: {target.description}",
        parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    )


def build_graph(
    specs: Sequence[AgentSpec],
    entry: str,
    toolbox: Toolbox,
    gateway: Gateway,
    config: AgentConfig,
    checkpointer: Any = None,
) -> Any:
    by_name = {s.name: s for s in specs}
    today = config.today

    def tools_for(spec: AgentSpec) -> list[ToolSpec]:
        handoffs = [_handoff_spec(s) for s in specs if s.name != spec.name]
        return toolbox.tools_for(list(spec.tools)) + handoffs

    async def agent_node(state: ConvState) -> dict[str, Any]:
        spec = by_name[state.get("agent") or entry]
        steps = state.get("steps", 0)
        tools = tools_for(spec) if steps < config.max_steps_per_turn else []
        history = [Message.model_validate(m) for m in state["messages"]]
        req = LLMRequest(
            model=config.model,
            messages=[Message(role="system", content=system_prompt(spec.role, today)), *history],
            tools=tools,
            temperature=config.temperature,
        )
        try:
            resp = await gateway.complete(
                req,
                conversation_id=state["conversation_id"],
                namespace=config.namespace,
                fallbacks=config.fallbacks,
            )
        except (BudgetExceeded, ProviderError) as e:
            text = "I'm sorry, I can't continue right now. A specialist will follow up with you."
            return {
                "messages": [{"role": "assistant", "content": text, "agent": spec.name}],
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "steps": steps + 1,
            }
        msg = resp.as_message().model_dump(exclude_none=True)
        msg["agent"] = spec.name
        unsupported = (
            not msg.get("tool_calls")
            and not state.get("acted")
            and claims_action(str(msg.get("content") or ""))
        )
        if not unsupported:
            return {"messages": [msg], "steps": steps + 1, "agent": spec.name}
        if state.get("claim_checked") or steps + 1 >= config.max_steps_per_turn:
            # Already coached once this turn, or no step left to retry.
            if state.get("ever_acted"):
                # Something was done earlier in the conversation, so this may be an honest restatement and
                # no fixed sentence is guaranteed true: send it (D16 limit).
                return {"messages": [msg], "steps": steps + 1, "agent": spec.name}
            msg["internal"] = True
            text = SAFE_REPLY_PENDING if state.get("pending") else SAFE_REPLY
            safe = {"role": "assistant", "content": text, "agent": spec.name}
            return {"messages": [msg, safe], "steps": steps + 1, "agent": spec.name}
        msg["internal"] = True  # held back: the customer never sees this reply
        return {"messages": [msg], "steps": steps + 1, "agent": spec.name}

    async def tools_node(state: ConvState) -> dict[str, Any]:
        spec = by_name[state.get("agent") or entry]
        last = Message.model_validate(state["messages"][-1])
        over_cap = state.get("steps", 0) > config.max_steps_per_turn
        allowed = [] if over_cap else [t.name for t in tools_for(spec)]
        new: list[dict[str, Any]] = []
        update: dict[str, Any] = {}
        pending = list(state.get("pending") or [])
        for tc in last.tool_calls:
            if tc.name.startswith(HANDOFF_PREFIX) and tc.name in allowed:
                target = tc.name.removeprefix(HANDOFF_PREFIX)
                update["agent"] = target
                update["handed_off"] = True
                content = compact({"ok": True, "handoff": target, "note": "Continue as the new specialist."})
                toolbox.record_handoff(state["conversation_id"], spec.name, target, tc.arguments)
                with span(f"handoff {spec.name} -> {target}", kind="CHAIN") as sp:
                    set_attrs(
                        sp,
                        {
                            "griddesk.handoff.from": spec.name,
                            "griddesk.handoff.to": target,
                            "griddesk.handoff.reason": str(tc.arguments.get("reason", ""))[:200],
                        },
                    )
            else:
                result = await toolbox.call(
                    tc.name,
                    tc.arguments,
                    conversation_id=state["conversation_id"],
                    agent=spec.name,
                    allowed=allowed,
                )
                err = result.get("error") or {}
                if err.get("code") == "needs_approval" and result.get("approval_request_id"):
                    # Parked for a supervisor is not done: "your credit has been applied" is still false.
                    pending.append({"id": result["approval_request_id"], "action": tc.name})
                elif result.get("ok") and tc.name not in READ_ONLY_TOOLS:
                    update["acted"] = True
                    update["ever_acted"] = True
                content = toolbox.render(tc.name, result, state["conversation_id"])
            new.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": content})
        return {"messages": new, "pending": pending, **update}

    async def approval_node(state: ConvState) -> dict[str, Any]:
        """Pause for one pending approval. One approval per node run, so a replayed node never
        re-executes an earlier approval. The outcome comes from the database, not from the resume value."""
        pending = list(state.get("pending") or [])
        first = pending[0]
        interrupt(
            {
                "approval_request_id": first["id"],
                "action": first["action"],
                "conversation_id": state["conversation_id"],
            }
        )
        result = await toolbox.execute_approved(first["action"], first["id"])
        status = result.get("status")
        acted = bool(state.get("acted"))
        ever_acted = bool(state.get("ever_acted"))
        if result.get("ok") and status == "executed":
            # The tool result is untrusted data like any other: filter and spotlight it, and keep only
            # the runtime's own sentence outside the block.
            rendered = toolbox.render(first["action"], result, state["conversation_id"])
            note = f"A supervisor approved request {first['id']} and it was completed. Result:\n{rendered}"
            pending = pending[1:]
            acted = True
            ever_acted = True
        elif status == "denied":
            note = f"A supervisor denied request {first['id']}. The action was not performed."
            pending = pending[1:]
        elif status == "pending":
            note = f"Request {first['id']} is still waiting for a supervisor. Nothing has been done yet."
        else:
            err = (result.get("error") or {}).get("message", "unknown error")
            note = f"Request {first['id']} could not be completed ({status}: {err}). Nothing was changed."
            pending = pending[1:]
        return {
            "messages": [{"role": "user", "content": RUNTIME_NOTICE + note}],
            "pending": pending,
            "steps": 0,
            "acted": acted,
            "ever_acted": ever_acted,
        }

    def nudge_node(state: ConvState) -> dict[str, Any]:
        note = {"role": "user", "content": RUNTIME_NOTICE + NUDGE_NOTE, "internal": True}
        return {"messages": [note], "nudged": True}

    def claim_node(state: ConvState) -> dict[str, Any]:
        note = {"role": "user", "content": RUNTIME_NOTICE + CLAIM_NOTE, "internal": True}
        return {"messages": [note], "claim_checked": True}

    def after_agent(state: ConvState) -> Literal["tools", "approval", "nudge", "claim_check", "__end__"]:
        last = state["messages"][-1]
        steps = state.get("steps", 0)
        if last.get("tool_calls") and steps <= config.max_steps_per_turn:
            return "tools"
        if last.get("internal") and last.get("role") == "assistant":
            # Before the approval pause, so a false "applied" written while a request waits is caught.
            return "claim_check"
        if state.get("pending"):
            return "approval"
        at_front_desk = len(specs) > 1 and (state.get("agent") or entry) == entry
        can_nudge = (
            at_front_desk
            and steps < config.max_steps_per_turn
            and not state.get("error")
            and not state.get("nudged")
            and not state.get("handed_off")
        )
        if can_nudge and promises_handoff(str(last.get("content") or "")):
            return "nudge"
        return "__end__"

    g = StateGraph(ConvState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("approval", approval_node)
    g.add_node("nudge", nudge_node)
    g.add_node("claim_check", claim_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges(
        "agent",
        after_agent,
        {
            "tools": "tools",
            "approval": "approval",
            "nudge": "nudge",
            "claim_check": "claim_check",
            "__end__": END,
        },
    )
    g.add_edge("tools", "agent")
    g.add_edge("approval", "agent")
    g.add_edge("nudge", "agent")
    g.add_edge("claim_check", "agent")
    return g.compile(checkpointer=checkpointer)
