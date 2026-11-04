"""Conversation runner: one object that owns the graph and answers customer turns."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from griddesk.agents.graph import AgentConfig, AgentSpec, build_graph, neutralize_customer_text
from griddesk.agents.toolbox import Toolbox
from griddesk.gateway import Gateway

BILLING_TOOLS = (
    "get_account_summary",
    "list_bills",
    "explain_bill",
    "apply_credit",
    "create_payment_plan",
    "dispute_bill",
    "request_meter_check",
)
ACCOUNT_TOOLS = ("verify_identity", "escalate_to_human")

Design = Literal["single"]


def agent_specs(design: Design) -> tuple[list[AgentSpec], str]:
    """The agent line-up for a design. "single" is one agent with every tool."""
    return [AgentSpec("agent", "billing", ACCOUNT_TOOLS + BILLING_TOOLS, "all customer requests")], "agent"


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
        # One turn at a time per conversation, so send and resume never race on the checkpoint.
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def _cfg(conversation_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": conversation_id}}

    async def send(self, conversation_id: str, text: str) -> TurnResult:
        async with self._locks[conversation_id]:
            if await self.awaiting_approval(conversation_id):
                # The conversation is paused on a supervisor; the customer's message waits with it.
                return TurnResult(
                    reply="Your request is still with a supervisor. I'll update you as soon as they decide.",
                    awaiting_approval=await self.awaiting_approval(conversation_id),
                )
            before = await self._message_count(conversation_id)
            await self.graph.ainvoke(
                {
                    "conversation_id": conversation_id,
                    "messages": [{"role": "user", "content": neutralize_customer_text(text)}],
                    "steps": 0,
                    "error": None,
                },
                self._cfg(conversation_id),
            )
            return await self._result(conversation_id, before + 1)

    async def resume(self, conversation_id: str) -> TurnResult:
        """Continue a conversation paused for approval, after an operator decided in the database."""
        async with self._locks[conversation_id]:
            if not await self.awaiting_approval(conversation_id):
                raise ValueError(f"conversation {conversation_id} is not waiting for an approval")
            before = await self._message_count(conversation_id)
            await self.graph.ainvoke(Command(resume={"resumed": True}), self._cfg(conversation_id))
            return await self._result(conversation_id, before)

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
