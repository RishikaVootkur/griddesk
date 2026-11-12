"""Conversation runner: one object that owns the graph and answers customer turns."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from griddesk.agents.graph import AgentConfig, AgentSpec, build_graph, neutralize_customer_text
from griddesk.agents.toolbox import Toolbox
from griddesk.gateway import Gateway
from griddesk.observability.tracing import set_attrs, span, trace_text

COMMON_TOOLS = ("verify_identity", "escalate_to_human", "search_policy")
BILLING_TOOLS = (
    "get_account_summary",
    "list_bills",
    "explain_bill",
    "apply_credit",
    "create_payment_plan",
    "dispute_bill",
    "request_meter_check",
)
OUTAGE_TOOLS = ("lookup_outage", "report_outage", "get_restoration_estimate")
ACCOUNT_TOOLS = (
    "get_account_summary",  # open orders and their dates, for P-22
    "get_contact_info",
    "update_contact_info",
    "set_paperless",
    "set_autopay",
    "lookup_address",
    "start_service",
    "stop_service",
    "transfer_service",
)

Design = Literal["single", "multi"]


def agent_specs(design: Design) -> tuple[list[AgentSpec], str]:
    """The agent line-up for a design.

    "single": one agent with every tool. "multi": a triage agent that routes to billing, outages and
    account specialists, each holding only its own tools (least privilege), with handoffs between them.
    """
    if design == "single":
        tools = tuple(dict.fromkeys(COMMON_TOOLS + BILLING_TOOLS + OUTAGE_TOOLS + ACCOUNT_TOOLS))
        return [AgentSpec("agent", "all", tools, "all customer requests")], "agent"
    return [
        AgentSpec("triage", "triage", ("search_policy", "escalate_to_human"), "routes the customer"),
        AgentSpec(
            "billing",
            "billing",
            COMMON_TOOLS + BILLING_TOOLS,
            "balances, bills, charges, late fees, credits, payment plans, disputes",
        ),
        AgentSpec(
            "outages",
            "outages",
            COMMON_TOOLS + OUTAGE_TOOLS + ("get_account_summary",),
            "outage status, outage reports, hazards, restoration estimates",
        ),
        AgentSpec(
            "account",
            "account",
            COMMON_TOOLS + ACCOUNT_TOOLS,
            "contact details, paperless, autopay, start, stop or move service",
        ),
    ], "triage"


@dataclass
class TurnResult:
    reply: str
    awaiting_approval: dict[str, Any] | None = None
    error: str | None = None
    agents: list[str] = field(default_factory=list)


class ConversationRunner:
    def __init__(
        self,
        toolbox: Toolbox,
        gateway: Gateway,
        config: AgentConfig,
        design: Design = "single",
        checkpointer: Any = None,
    ) -> None:
        specs, entry = agent_specs(design)
        self.toolbox = toolbox
        self.gateway = gateway
        self.graph = build_graph(specs, entry, toolbox, gateway, config, checkpointer or InMemorySaver())
        # One turn at a time per conversation, so send and resume never race on the checkpoint. Weak
        # values: a lock lives only while some turn holds or waits on it, so idle chats cost nothing.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    @staticmethod
    def _cfg(conversation_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": conversation_id}}

    async def send(self, conversation_id: str, text: str) -> TurnResult:
        async with self.lock(conversation_id):
            if await self.awaiting_approval(conversation_id):
                # The conversation is paused on a supervisor; the customer's message waits with it.
                return TurnResult(
                    reply="Your request is still with a supervisor. I'll update you as soon as they decide.",
                    awaiting_approval=await self.awaiting_approval(conversation_id),
                )
            before = await self._message_count(conversation_id)
            with span("invoke_agent griddesk", kind="AGENT") as sp:
                set_attrs(
                    sp,
                    {
                        "gen_ai.operation.name": "invoke_agent",
                        "gen_ai.conversation.id": conversation_id,
                        "input.value": trace_text(text),
                    },
                )
                result = await self._send(conversation_id, text, before)
                set_attrs(
                    sp,
                    {"output.value": trace_text(result.reply), "gen_ai.agent.name": ",".join(result.agents)},
                )
                return result

    async def _send(self, conversation_id: str, text: str, before: int) -> TurnResult:
        await self.graph.ainvoke(
            {
                "conversation_id": conversation_id,
                "messages": [{"role": "user", "content": neutralize_customer_text(text)}],
                "steps": 0,
                "nudged": False,
                "handed_off": False,
                "error": None,
            },
            self._cfg(conversation_id),
        )
        return await self._result(conversation_id, before + 1)

    async def resume(self, conversation_id: str) -> TurnResult:
        """Continue a conversation paused for approval, after an operator decided in the database."""
        async with self.lock(conversation_id):
            if not await self.awaiting_approval(conversation_id):
                raise ValueError(f"conversation {conversation_id} is not waiting for an approval")
            before = await self._message_count(conversation_id)
            with span("invoke_agent griddesk resume", kind="AGENT") as sp:
                set_attrs(
                    sp, {"gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": conversation_id}
                )
                await self.graph.ainvoke(Command(resume={"resumed": True}), self._cfg(conversation_id))
                result = await self._result(conversation_id, before)
                set_attrs(sp, {"output.value": trace_text(result.reply)})
                return result

    async def awaiting_approval(self, conversation_id: str) -> dict[str, Any] | None:
        snap = await self.graph.aget_state(self._cfg(conversation_id))
        if not snap or "approval" not in (snap.next or ()):
            return None
        for task in snap.tasks:
            for intr in task.interrupts:
                return dict(intr.value)
        return None

    async def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        snap = await self.graph.aget_state(self._cfg(conversation_id))
        return list(snap.values.get("messages", [])) if snap and snap.values else []

    async def _message_count(self, conversation_id: str) -> int:
        return len(await self.messages(conversation_id))

    async def _result(self, conversation_id: str, start: int) -> TurnResult:
        snap = await self.graph.aget_state(self._cfg(conversation_id))
        msgs = snap.values.get("messages", [])[start:]
        texts = [m["content"] for m in msgs if m.get("role") == "assistant" and m.get("content")]
        agents = [m["agent"] for m in msgs if m.get("role") == "assistant" and m.get("agent")]
        pending = None
        for task in snap.tasks:
            for intr in task.interrupts:
                pending = intr.value
        return TurnResult(
            reply="\n\n".join(texts), awaiting_approval=pending, error=snap.values.get("error"), agents=agents
        )
