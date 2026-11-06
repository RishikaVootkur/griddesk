import asyncio
from collections import deque
from datetime import date
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from griddesk.agents.graph import AgentConfig
from griddesk.agents.runtime import ConversationRunner
from griddesk.agents.toolbox import Toolbox
from griddesk.gateway import Gateway, LLMRequest, LLMResponse, Message, ToolSpec, Usage
from griddesk.observability.tracing import set_attrs, span

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def provider():
    p = TracerProvider()
    p.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(p)
    yield


@pytest.fixture(autouse=True)
def clear():
    EXPORTER.clear()


class Fake:
    name = "gemini"

    async def complete(self, req):
        return LLMResponse(
            model=req.model,
            content="Your balance is $12.00, jo@example.com",
            usage=Usage(input_tokens=1000, output_tokens=500),
        )


def test_llm_span_carries_genai_usage_cost_and_redacted_output():
    gw = Gateway(providers={"gemini": Fake()})
    req = LLMRequest(model="gemini:gemini-3.8-flash", messages=[Message(role="user", content="hi")])
    asyncio.run(gw.complete(req, conversation_id="c1"))
    (sp,) = EXPORTER.get_finished_spans()
    a = sp.attributes
    assert sp.name == "chat gemini:gemini-3.8-flash"
    assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.request.model"] == "gemini:gemini-3.8-flash"
    assert a["gen_ai.usage.input_tokens"] == 1000 and a["gen_ai.usage.output_tokens"] == 500
    assert a["griddesk.cost_usd_list_price_equivalent"] == pytest.approx(0.002625)
    assert a["openinference.span.kind"] == "LLM"
    assert "jo@example.com" not in a["output.value"]


def test_set_attrs_redacts_nested_values_and_skips_none():
    with span("execute_tool x", kind="TOOL") as sp:
        set_attrs(sp, {"tool.parameters": {"email": "a@b.com"}, "missing": None, "n": 3})
    (s,) = EXPORTER.get_finished_spans()
    assert "a@b.com" not in s.attributes["tool.parameters"] and "missing" not in s.attributes
    assert s.attributes["n"] == 3


def test_errors_mark_the_span():
    with pytest.raises(ValueError), span("boom"):
        raise ValueError("x")
    (s,) = EXPORTER.get_finished_spans()
    assert s.status.status_code.name == "ERROR"


class _SlowClient:
    """A fake MCP client: each call sleeps, so two conversations' calls interleave."""

    async def call_tool(self, name, payload):
        conv = payload["conversation_id"]
        await asyncio.sleep(0.05 if conv == "conv-a" else 0.01)
        out = {"ok": True} if conv == "conv-a" else {"ok": False, "error": {"code": "not_found"}}
        return SimpleNamespace(is_error=False, structured_content=out, content=[])


def _toolbox(history=None):
    props = {"type": "object", "properties": {"account_id": {"type": "string"}}}
    return Toolbox(
        clients={"billing": _SlowClient()},
        specs={"get_account_summary": ToolSpec(name="get_account_summary", description="", parameters=props)},
        server_of={"get_account_summary": "billing"},
        calls=deque(maxlen=history),
    )


def test_concurrent_tool_calls_keep_their_own_span_attributes():
    tb = _toolbox()

    async def both():
        tool = "get_account_summary"
        await asyncio.gather(
            tb.call(tool, {"account_id": "A-111"}, conversation_id="conv-a", agent="billing"),
            tb.call(tool, {"account_id": "B-222"}, conversation_id="conv-b", agent="billing"),
        )

    asyncio.run(both())
    spans = {s.attributes["gen_ai.conversation.id"]: s.attributes for s in EXPORTER.get_finished_spans()}
    # conv-b finishes first and appends its log while conv-a is still waiting on its call.
    assert "A-111" in spans["conv-a"]["tool.parameters"] and "B-222" not in spans["conv-a"]["tool.parameters"]
    assert "B-222" in spans["conv-b"]["tool.parameters"]
    assert "griddesk.tool.error_code" not in spans["conv-a"]
    assert spans["conv-b"]["griddesk.tool.error_code"] == "not_found"


def test_toolbox_history_is_bounded():
    tb = _toolbox(history=2)
    for i in range(5):
        asyncio.run(tb.call("get_account_summary", {"account_id": str(i)}, conversation_id="c", agent="x"))
    assert len(tb.calls) == 2 and tb.calls[-1].arguments == {"account_id": "4"}
    off = _toolbox(history=0)
    asyncio.run(off.call("get_account_summary", {}, conversation_id="c", agent="x"))
    assert len(off.calls) == 0


def test_turn_spans_never_hold_identity_answers():
    class Echo:
        name = "fake"

        async def complete(self, req):
            return LLMResponse(model=req.model, content="Thanks, last four 1234 and DOB 1980-02-03 noted.")

    runner = ConversationRunner(
        Toolbox(), Gateway(providers={"fake": Echo()}), AgentConfig(model="fake:m", today=date(2026, 10, 15))
    )
    asyncio.run(runner.send("c-1", "My last four are 1234, born 1980-02-03"))
    attrs = [dict(s.attributes) for s in EXPORTER.get_finished_spans()]
    turn = next(a for a in attrs if a.get("gen_ai.operation.name") == "invoke_agent")
    assert turn["input.value"].startswith("My last four are ####")
    for a in attrs:
        for v in a.values():
            assert "1234" not in str(v) and "1980" not in str(v)
