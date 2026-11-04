"""MCP client side: connect to the GridDesk servers and expose their tools to the agents.

The toolbox is the boundary between model output and the tools:
- It strips `conversation_id` from the schemas the model sees and always injects the real one.
- It never shows tools tagged `griddesk/internal`.
- It records every call (tool, arguments, schema validity, outcome) for the eval's tool-call
  accuracy and policy metrics.
"""

from __future__ import annotations

import copy
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import Client
from mcp.server.mcpserver import MCPServer

from griddesk.gateway import ToolSpec
from griddesk.logging import redact_value
from griddesk.mcp_servers.common import INTERNAL_KEY

RUNTIME_ARGS = {"conversation_id"}
SENSITIVE_ARGS = {"date_of_birth", "ssn_last4"}


@dataclass
class ToolCallLog:
    conversation_id: str
    agent: str
    tool: str
    arguments: dict[str, Any]
    known_tool: bool
    schema_valid: bool
    ok: bool
    error_code: str | None = None
    rule_id: str | None = None


@dataclass
class Toolbox:
    """Holds connected MCP clients. Use `async with Toolbox.connect(...)`."""

    clients: dict[str, Client] = field(default_factory=dict)
    specs: dict[str, ToolSpec] = field(default_factory=dict)
    server_of: dict[str, str] = field(default_factory=dict)
    internal: dict[str, set[str]] = field(default_factory=dict)  # server -> runtime-only tool names
    calls: list[ToolCallLog] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    _stack: AsyncExitStack | None = None

    @classmethod
    async def open(cls, servers: dict[str, MCPServer | str]) -> Toolbox:
        """Connect to each server: an in-process `MCPServer` or a Streamable HTTP URL."""
        tb = cls()
        tb._stack = AsyncExitStack()
        for name, target in servers.items():
            client = await tb._stack.enter_async_context(Client(target))
            tb.clients[name] = client
            for t in (await client.list_tools()).tools:
                if (t.meta or {}).get(INTERNAL_KEY):
                    tb.internal.setdefault(name, set()).add(t.name)
                    continue
                if t.name in tb.specs:
                    raise ValueError(f"tool {t.name} is exposed by both {tb.server_of[t.name]} and {name}")
                schema = copy.deepcopy(t.input_schema)
                for arg in RUNTIME_ARGS:
                    schema.get("properties", {}).pop(arg, None)
                    if arg in schema.get("required", []):
                        schema["required"] = [r for r in schema["required"] if r != arg]
                schema.pop("title", None)
                tb.specs[t.name] = ToolSpec(name=t.name, description=t.description or "", parameters=schema)
                tb.server_of[t.name] = name
        return tb

    async def close(self) -> None:
        if self._stack:
            await self._stack.aclose()

    def tools_for(self, names: list[str]) -> list[ToolSpec]:
        return [self.specs[n] for n in names if n in self.specs]

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        conversation_id: str,
        agent: str,
        allowed: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run a model-requested tool call and return its structured result."""
        args = {k: v for k, v in arguments.items() if k not in RUNTIME_ARGS}
        # Call logs feed run files: identity answers are masked, everything else goes through redaction.
        logged = redact_value({k: ("<redacted>" if k in SENSITIVE_ARGS else v) for k, v in args.items()})
        if name not in self.specs or (allowed is not None and name not in allowed):
            self.calls.append(
                ToolCallLog(conversation_id, agent, name, logged, False, False, False, "unknown_tool")
            )
            return {
                "ok": False,
                "error": {"code": "invalid_request", "message": f"No tool named {name} here."},
            }
        client = self.clients[self.server_of[name]]
        props = self.specs[name].parameters.get("properties", {})
        # Every model-facing GridDesk tool takes conversation_id; the runtime value always wins.
        payload = {**args, "conversation_id": conversation_id}
        result = await client.call_tool(name, payload)
        if result.is_error or result.structured_content is None:
            text = " ".join(getattr(c, "text", "") for c in result.content)[:400]
            self.calls.append(
                ToolCallLog(conversation_id, agent, name, logged, True, False, False, "invalid_request")
            )
            return {
                "ok": False,
                "error": {"code": "invalid_request", "message": text or "Invalid arguments."},
            }
        out: dict[str, Any] = result.structured_content
        err = out.get("error") or {}
        unknown_args = set(args) - set(props)
        self.calls.append(
            ToolCallLog(
                conversation_id,
                agent,
                name,
                logged,
                True,
                not unknown_args,
                bool(out.get("ok")),
                err.get("code"),
                err.get("rule_id"),
            )
        )
        return out

    async def execute_approved(self, action: str, approval_request_id: str) -> dict[str, Any]:
        """Run an operator-approved action on the server that owns it (runtime only)."""
        server = self.server_of.get(action)
        if server is None or "execute_approved_action" not in self.internal.get(server, set()):
            return {"ok": False, "error": {"code": "invalid_request", "message": f"Cannot execute {action}."}}
        result = await self.clients[server].call_tool(
            "execute_approved_action", {"approval_request_id": approval_request_id}
        )
        if result.is_error or result.structured_content is None:
            text = " ".join(getattr(c, "text", "") for c in result.content)[:300]
            return {"ok": False, "error": {"code": "invalid_request", "message": text or "execution failed"}}
        return result.structured_content

    def record_handoff(
        self, conversation_id: str, source: str, target: str, arguments: dict[str, Any]
    ) -> None:
        self.handoffs.append(
            {
                "conversation_id": conversation_id,
                "from": source,
                "to": target,
                "reason": str(arguments.get("reason", ""))[:200],
            }
        )


def compact(result: dict[str, Any]) -> str:
    """Tool result as compact JSON for the model, dropping empty fields to save tokens."""

    def prune(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: prune(x) for k, x in v.items() if x not in (None, [], {}, "")}
        if isinstance(v, list):
            return [prune(x) for x in v]
        return v

    return json.dumps(prune(result), separators=(",", ":"), sort_keys=True)
