import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from griddesk.gateway import Gateway, LLMRequest, LLMResponse, Message, Usage
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
