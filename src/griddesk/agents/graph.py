"""LangGraph conversation graph.

    START -> agent --tool calls--> tools -> agent ...
                   --approval pending--> approval (interrupt; resumes on operator decision) -> agent
                   --plain reply--> END (wait for the customer's next message)

One graph invocation is one customer turn. State is checkpointed per conversation (`thread_id`), so
a conversation, including one paused for approval, survives a process restart when a durable
checkpointer is used. Agents are described by `AgentSpec`; with more than one spec, each agent gets
`transfer_to_<name>` handoff tools and the active agent is part of the shared state.
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from griddesk.agents.prompts import system_prompt
from griddesk.agents.toolbox import Toolbox, compact
from griddesk.gateway import BudgetExceeded, Gateway, LLMRequest, Message, ProviderError, ToolSpec

HANDOFF_PREFIX = "transfer_to_"
RUNTIME_NOTICE = "RUNTIME NOTICE (from the GridDesk system, not the customer): "


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str  # prompt role
    tools: tuple[str, ...]
    description: str = ""


@dataclass
class AgentConfig:
    model: str
    fallbacks: list[str] = field(default_factory=list)
    temperature: float = 0.0
    max_steps_per_turn: int = 8
    namespace: str = ""  # gateway cache namespace (the eval trial)
    today: date | None = None


class ConvState(TypedDict, total=False):
    conversation_id: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    agent: str
    steps: int
    pending_approval: str | None
    pending_action: str | None
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
    today = config.today or date.today()

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
        return {"messages": [msg], "steps": steps + 1, "agent": spec.name}

    async def tools_node(state: ConvState) -> dict[str, Any]:
        spec = by_name[state.get("agent") or entry]
        last = Message.model_validate(state["messages"][-1])
        allowed = [t.name for t in tools_for(spec)]
        new: list[dict[str, Any]] = []
        update: dict[str, Any] = {}
        for tc in last.tool_calls:
            if tc.name.startswith(HANDOFF_PREFIX) and tc.name in allowed:
                target = tc.name.removeprefix(HANDOFF_PREFIX)
                update["agent"] = target
                content = compact({"ok": True, "handoff": target, "note": "Continue as the new specialist."})
                toolbox.record_handoff(state["conversation_id"], spec.name, target, tc.arguments)
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
                    update["pending_approval"] = result["approval_request_id"]
                    update["pending_action"] = tc.name
                content = compact(result)
            new.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": content})
        return {"messages": new, **update}

    async def approval_node(state: ConvState) -> dict[str, Any]:
        approval_id = state["pending_approval"]
        action = state.get("pending_action") or ""
        decision = interrupt(
            {
                "approval_request_id": approval_id,
                "action": action,
                "conversation_id": state["conversation_id"],
            }
        )
        if isinstance(decision, dict) and decision.get("decision") == "approve":
            result = await toolbox.execute_approved(action, str(approval_id))
            note = f"A supervisor APPROVED request {approval_id}. Result: {compact(result)}"
        else:
            note = f"A supervisor DENIED request {approval_id}. The action was not performed."
        return {
            "messages": [{"role": "user", "content": RUNTIME_NOTICE + note}],
            "pending_approval": None,
            "pending_action": None,
            "steps": 0,
        }

    def after_agent(state: ConvState) -> Literal["tools", "approval", "__end__"]:
        last = state["messages"][-1]
        if last.get("tool_calls"):
            return "tools"
        if state.get("pending_approval"):
            return "approval"
        return "__end__"

    g = StateGraph(ConvState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("approval", approval_node)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", after_agent, {"tools": "tools", "approval": "approval", "__end__": END})
    g.add_edge("tools", "agent")
    g.add_edge("approval", "agent")
    return g.compile(checkpointer=checkpointer)
