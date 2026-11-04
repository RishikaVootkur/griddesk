"""OpenTelemetry tracing with the GenAI semantic conventions, exported to Arize Phoenix.

Spans:
- `invoke_agent <agent>`: one per customer turn (gen_ai.operation.name = invoke_agent).
- `chat <model>`: one per LLM call, with gen_ai.request.model, gen_ai.response.model,
  gen_ai.usage.input_tokens / output_tokens, latency, list-price-equivalent cost and cache hit.
- `execute_tool <tool>`: one per tool call (gen_ai.tool.name), with the outcome and rule id.
- `handoff <from> -> <to>` and `guardrail <kind>`: one per handoff and per guardrail decision.

Every string attribute passes through the installed PII redactor (Presidio in the API) before it is
set, so traces never hold raw personal data. When tracing is not configured the tracer is a no-op.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from griddesk.logging import redact_value

TRACER_NAME = "griddesk"
_configured = False


def setup_tracing(endpoint: str, service_name: str = "griddesk") -> None:
    """Send spans to an OTLP/HTTP collector such as Phoenix (http://localhost:6006/v1/traces)."""
    global _configured
    if _configured:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def set_attrs(span: Span, attrs: dict[str, Any]) -> None:
    """Set attributes, redacting strings and dropping Nones (OTel rejects None values)."""
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, str | dict | list | tuple):
            v = redact_value(value)
            if isinstance(v, dict | list):
                v = str(v)
            span.set_attribute(key, v)
        else:
            span.set_attribute(key, value)


@contextmanager
def span(name: str, attrs: dict[str, Any] | None = None, kind: str | None = None) -> Iterator[Span]:
    """Start a span; `kind` sets the OpenInference span kind so Phoenix renders it nicely."""
    with tracer().start_as_current_span(name) as sp:
        if kind:
            sp.set_attribute("openinference.span.kind", kind)
        if attrs:
            set_attrs(sp, attrs)
        try:
            yield sp
        except Exception as e:
            sp.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


def record_llm_call(
    span_: Span,
    *,
    provider: str,
    request_model: str,
    response_model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    cost_usd: float,
    cached: bool,
    attempts: int,
    temperature: float,
    conversation_id: str | None,
    output_preview: str = "",
) -> None:
    set_attrs(
        span_,
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": request_model,
            "gen_ai.response.model": response_model,
            "gen_ai.request.temperature": temperature,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "gen_ai.conversation.id": conversation_id,
            "llm.token_count.prompt": input_tokens,
            "llm.token_count.completion": output_tokens,
            "llm.token_count.total": input_tokens + output_tokens,
            "llm.model_name": response_model,
            "griddesk.latency_ms": latency_ms,
            "griddesk.cost_usd_list_price_equivalent": round(cost_usd, 8),
            "griddesk.cache_hit": cached,
            "griddesk.attempts": attempts,
            "output.value": output_preview[:500],
        },
    )
