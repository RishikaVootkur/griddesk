"""Helpers for tool and MCP server tests."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from griddesk.db import models as m
from griddesk.runtime import Clock, IdGenerator
from griddesk.tools.common import ToolContext


def make_ctx(sessions, enforce: bool = True) -> ToolContext:
    return ToolContext(
        sessions=sessions, clock=Clock(deterministic=True), ids=IdGenerator("test"), enforce_policy=enforce
    )


def verify(ctx: ToolContext, conversation_id: str, customer_id: str) -> None:
    with ctx.sessions() as s:
        s.add(
            m.IdentityVerification(
                conversation_id=conversation_id,
                customer_id=customer_id,
                success=True,
                method="test",
                attempted_at=ctx.clock.now(),
            )
        )
        s.commit()


def one(ctx: ToolContext, sql: str, **params: Any) -> Any:
    with ctx.sessions() as s:
        row = s.execute(text(sql), params).first()
        assert row is not None, f"no fixture row for: {sql}"
        return row


async def call(client, tool: str, **args: Any) -> dict[str, Any]:
    r = await client.call_tool(tool, args)
    assert r.structured_content is not None, r.content
    return r.structured_content
