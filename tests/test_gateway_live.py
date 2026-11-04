"""Live provider checks. Skipped unless GRIDDESK_LIVE=1, so CI never spends quota or needs keys."""

import asyncio
import os

import pytest

from griddesk.gateway import LLMRequest, Message, ToolSpec, build_gateway

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("GRIDDESK_LIVE") != "1", reason="set GRIDDESK_LIVE=1 to call real providers"
    ),
]

TOOL = ToolSpec(
    name="get_balance",
    description="Get the balance for an account.",
    parameters={
        "type": "object",
        "properties": {"account_id": {"type": "string"}},
        "required": ["account_id"],
    },
)


@pytest.mark.parametrize("model", ["gemini:gemini-3.6-flash", "groq:qwen/qwen3.8-27b", "ollama:qwen3:8b"])
def test_tool_round_trip(model, tmp_path):
    gw = build_gateway(cache_dir=tmp_path, cache_mode="off")

    async def go():
        msgs = [
            Message(role="system", content="You are a utility agent. Always use tools for account data."),
            Message(role="user", content="What's the balance on account 1000123456?"),
        ]
        r1 = await gw.complete(LLMRequest(model=model, messages=msgs, tools=[TOOL]))
        assert r1.tool_calls and r1.tool_calls[0].name == "get_balance"
        call = r1.tool_calls[0]
        msgs += [
            r1.as_message(),
            Message(role="tool", tool_call_id=call.id, name=call.name, content='{"balance": "123.45"}'),
        ]
        r2 = await gw.complete(LLMRequest(model=model, messages=msgs, tools=[TOOL]))
        assert "123.45" in r2.content
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert r1.usage.input_tokens > 0 and r2.latency_ms > 0
