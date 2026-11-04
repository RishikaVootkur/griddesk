import asyncio
import random

import pytest

from griddesk.gateway import (
    BudgetExceeded,
    CacheMiss,
    Gateway,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    RateLimit,
    ToolCall,
    ToolSpec,
    Usage,
)
from griddesk.gateway.cache import DiskCache, request_key
from griddesk.gateway.pricing import list_price_cost
from griddesk.gateway.providers import GeminiProvider, OllamaProvider, _openai_messages, _parse_retry_after


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeProvider:
    def __init__(self, name, script):
        self.name = name
        self.script = list(script)  # each item: LLMResponse or an exception to raise
        self.calls = 0

    async def complete(self, req):
        self.calls += 1
        item = self.script.pop(0) if self.script else LLMResponse(model=req.model, content="ok")
        if isinstance(item, Exception):
            raise item
        return item.model_copy(update={"model": req.model})


def req(model="gemini:gemini-3.8-flash", text="hi"):
    return LLMRequest(model=model, messages=[Message(role="user", content=text)])


def resp(inp=100, out=20, content="answer"):
    return LLMResponse(model="x", content=content, usage=Usage(input_tokens=inp, output_tokens=out))


def make(providers, tmp_path=None, **kw):
    clock = FakeClock()
    gw = Gateway(
        providers={p.name: p for p in providers},
        cache=DiskCache(tmp_path) if tmp_path else None,
        sleep=clock.sleep,
        clock=clock,
        rng=random.Random(0),
        **kw,
    )
    return gw, clock


def run(coro):
    return asyncio.run(coro)


def test_cost_is_list_price_equivalent():
    # 1,000 in + 500 out on gemini-3.8-flash at $0.75 / $3.75 per 1M = 0.00075 + 0.001875
    u = Usage(input_tokens=1000, output_tokens=500)
    assert list_price_cost("gemini:gemini-3.8-flash", u) == pytest.approx(0.002625)
    assert list_price_cost("groq:openai/gpt-oss-120b", u) == pytest.approx(0.00015 + 0.0003)
    assert list_price_cost("ollama:qwen3:8b", u) == 0.0
    assert list_price_cost("unknown:model", u) == 0.0


def test_success_records_tokens_and_cost():
    p = FakeProvider("gemini", [resp(1000, 500)])
    gw, _ = make([p])
    r = run(gw.complete(req(), conversation_id="c1"))
    assert r.content == "answer" and r.cost_usd == pytest.approx(0.002625) and r.attempts == 1
    assert gw.tokens_used("c1") == 1500
    assert gw.records[-1].model == "gemini:gemini-3.8-flash" and not gw.records[-1].cached


def test_retries_with_exponential_backoff_then_succeeds():
    errs = [ProviderError("429", status=429, retryable=True) for _ in range(3)]
    p = FakeProvider("gemini", [*errs, resp()])
    gw, clock = make([p], backoff_base=2.0)
    r = run(gw.complete(req()))
    assert r.attempts == 4 and p.calls == 4
    # base * 2^(n-1) scaled by jitter in [0.5, 1.0]
    for i, s in enumerate(clock.sleeps):
        assert 2.0 * 2**i * 0.5 <= s <= 2.0 * 2**i


def test_retry_after_is_honored():
    p = FakeProvider("gemini", [ProviderError("429", status=429, retryable=True, retry_after=30.0), resp()])
    gw, clock = make([p], backoff_base=1.0)
    run(gw.complete(req()))
    assert clock.sleeps[0] >= 30.0


def test_non_retryable_error_falls_back_immediately():
    bad = FakeProvider("gemini", [ProviderError("400 bad request", status=400)])
    good = FakeProvider("groq", [resp()])
    gw, clock = make([bad, good])
    r = run(gw.complete(req(), fallbacks=["groq:qwen/qwen3.8-27b"]))
    assert r.model == "groq:qwen/qwen3.8-27b" and r.fallback_from == "gemini:gemini-3.8-flash"
    assert bad.calls == 1 and clock.sleeps == []
    assert gw.records[0].error and gw.records[1].error is None


def test_fallback_chain_order_after_retries_exhausted():
    flaky = FakeProvider("gemini", [ProviderError("503", status=503, retryable=True)] * 10)
    also = FakeProvider("groq", [ProviderError("503", status=503, retryable=True)] * 10)
    local = FakeProvider("ollama", [resp()])
    gw, _ = make([flaky, also, local], max_retries=2)
    r = run(gw.complete(req(), fallbacks=["groq:qwen/qwen3.8-27b", "ollama:qwen3:8b"]))
    assert r.model == "ollama:qwen3:8b"
    assert flaky.calls == 3 and also.calls == 3 and local.calls == 1


def test_all_fail_raises_last_error():
    p = FakeProvider("gemini", [ProviderError("400", status=400)])
    gw, _ = make([p])
    with pytest.raises(ProviderError):
        run(gw.complete(req()))


def test_cache_hit_skips_provider_and_keeps_original_latency(tmp_path):
    p = FakeProvider("gemini", [resp()])
    gw, _ = make([p], tmp_path)
    first = run(gw.complete(req()))
    second = run(gw.complete(req()))
    assert p.calls == 1 and second.cached and not first.cached
    assert second.content == first.content and second.latency_ms == first.latency_ms


def test_cache_key_covers_full_request_and_namespace():
    a = req(text="hello")
    assert request_key(a) == request_key(req(text="hello"))
    assert request_key(a) != request_key(req(text="hello!"))
    assert request_key(a) != request_key(a.model_copy(update={"temperature": 0.7}))
    assert request_key(a, "trial-1") != request_key(a, "trial-2")


def test_trials_in_different_namespaces_are_independent(tmp_path):
    p = FakeProvider("gemini", [resp(content="one"), resp(content="two")])
    gw, _ = make([p], tmp_path)
    r1 = run(gw.complete(req(), namespace="t1"))
    r2 = run(gw.complete(req(), namespace="t2"))
    assert (r1.content, r2.content) == ("one", "two")


def test_replay_only_raises_on_miss(tmp_path):
    p = FakeProvider("gemini", [resp()])
    gw, _ = make([p], tmp_path, cache_mode="replay_only")
    with pytest.raises(CacheMiss):
        run(gw.complete(req()))
    assert p.calls == 0


def test_budget_blocks_once_exhausted():
    p = FakeProvider("gemini", [resp(600, 100), resp(600, 100)])
    gw, _ = make([p], conversation_budget=1000)
    run(gw.complete(req(), conversation_id="c"))  # 700 used
    run(gw.complete(req(), conversation_id="c"))  # 1400 used, allowed because it started under budget
    with pytest.raises(BudgetExceeded):
        run(gw.complete(req(), conversation_id="c"))
    run(gw.complete(req(), conversation_id="other"))  # budgets are per conversation


def test_rate_limiter_waits_when_rpm_exhausted():
    p = FakeProvider("groq", [resp() for _ in range(3)])
    gw, clock = make([p], limits={"groq": RateLimit(rpm=2)})
    for _ in range(3):
        run(gw.complete(req("groq:qwen/qwen3.8-27b")))
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(60.0)


def test_retry_after_parsing():
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("1.5s") == 1.5
    assert _parse_retry_after("500ms") == 0.5
    assert _parse_retry_after("2m") == 120.0
    assert _parse_retry_after("soon") is None


def test_gemini_wire_format_merges_parallel_tool_results_and_keeps_signatures():
    msgs = [
        Message(role="system", content="rules"),
        Message(role="user", content="q"),
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(id="a", name="f", arguments={"x": 1}, signature="SIG"),
                ToolCall(id="b", name="g", arguments={}),
            ],
        ),
        Message(role="tool", tool_call_id="a", name="f", content='{"ok": true}'),
        Message(role="tool", tool_call_id="b", name="g", content="plain"),
    ]
    system, contents = GeminiProvider.to_contents(msgs)
    assert system == "rules"
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "SIG"
    assert contents[1]["parts"][1]["thoughtSignature"] == "skip_thought_signature_validator"
    results = contents[2]["parts"]
    assert len(results) == 2 and results[0]["functionResponse"]["response"] == {"result": {"ok": True}}
    assert results[1]["functionResponse"]["response"] == {"result": "plain"}


def test_openai_and_ollama_wire_formats():
    msgs = [
        Message(role="assistant", tool_calls=[ToolCall(id="a", name="f", arguments={"x": 1})]),
        Message(role="tool", tool_call_id="a", name="f", content="{}"),
    ]
    oa = _openai_messages(msgs)
    assert oa[0]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
    assert oa[1] == {"role": "tool", "tool_call_id": "a", "content": "{}"}
    ol = OllamaProvider.to_messages(msgs)
    assert ol[0]["tool_calls"][0]["function"]["arguments"] == {"x": 1}
    assert ol[1]["tool_name"] == "f"


def test_tool_spec_round_trips():
    t = ToolSpec(name="f", description="d", parameters={"type": "object", "properties": {}})
    assert LLMRequest(model="groq:x", messages=[], tools=[t]).tools[0].name == "f"


def test_replay_finds_the_fallback_models_recording(tmp_path):
    bad = FakeProvider("gemini", [ProviderError("400", status=400)])
    good = FakeProvider("groq", [resp(content="from groq")])
    gw, _ = make([bad, good], tmp_path)
    run(gw.complete(req(), fallbacks=["groq:qwen/qwen3.8-27b"]))
    replay, _ = make(
        [FakeProvider("gemini", []), FakeProvider("groq", [])], tmp_path, cache_mode="replay_only"
    )
    r = run(replay.complete(req(), fallbacks=["groq:qwen/qwen3.8-27b"]))
    assert r.cached and r.content == "from groq"


def test_replay_raises_cache_miss_when_whole_chain_misses(tmp_path):
    gw, _ = make([FakeProvider("gemini", []), FakeProvider("groq", [])], tmp_path, cache_mode="replay_only")
    with pytest.raises(CacheMiss):
        run(gw.complete(req(), fallbacks=["groq:qwen/qwen3.8-27b"]))


def test_retry_limit_caps_retries_for_that_error():
    p = FakeProvider(
        "groq", [ProviderError("tool_use_failed", status=400, retryable=True, retry_limit=1)] * 5
    )
    gw, _ = make([p], max_retries=6)
    with pytest.raises(ProviderError):
        run(gw.complete(req("groq:qwen/qwen3.8-27b")))
    assert p.calls == 2


def test_invalid_cache_mode_is_rejected():
    with pytest.raises(ValueError):
        Gateway(providers={}, cache_mode="replay")


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    cache = DiskCache(tmp_path)
    key = request_key(req())
    cache.put(key, resp())
    path = tmp_path / key[:2] / f"{key}.json"
    path.write_text("<<<<<<< HEAD\n{broken")
    assert cache.get(key) is None


def test_provider_fingerprint_changes_the_key():
    assert request_key(req(), "", "thinking=low") != request_key(req(), "", "thinking=high")


def test_rate_slot_is_taken_after_the_concurrency_slot():
    """With concurrency 1 and slow calls, no 60 s window may hold more than rpm sends."""

    class Slow:
        name = "groq"

        def __init__(self, clock):
            self.clock = clock
            self.sent: list[float] = []

        async def complete(self, r):
            self.sent.append(self.clock.t)
            await self.clock.sleep(20)
            return resp().model_copy(update={"model": r.model})

    clock = FakeClock()
    p = Slow(clock)
    gw = Gateway(
        providers={"groq": p},
        sleep=clock.sleep,
        clock=clock,
        limits={"groq": RateLimit(rpm=2, concurrency=1)},
    )

    async def go():
        await asyncio.gather(*(gw.complete(req("groq:qwen/qwen3.8-27b")) for _ in range(5)))

    run(go())
    for t in p.sent:
        assert sum(1 for u in p.sent if t <= u < t + 60) <= 2


def test_tpm_limit_waits():
    p = FakeProvider("groq", [resp() for _ in range(2)])
    gw, clock = make([p], limits={"groq": RateLimit(rpm=100, tpm=300)})
    big = req("groq:qwen/qwen3.8-27b", text="x" * 800)  # ~200 + 256 estimated tokens
    run(gw.complete(big))
    run(gw.complete(big))
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(60.0)


class _FakeHTTP:
    """Stands in for httpx.AsyncClient.post with a canned response."""

    def __init__(self, status, body, headers=None):
        import httpx

        self.resp = httpx.Response(
            status,
            content=body if isinstance(body, bytes) else body.encode(),
            headers=headers or {},
            request=httpx.Request("POST", "http://x"),
        )

    async def post(self, *a, **k):
        return self.resp


def _gemini(body, status=200):
    return GeminiProvider("k", _FakeHTTP(status, body))  # type: ignore[arg-type]


def test_non_json_200_is_a_retryable_provider_error():
    with pytest.raises(ProviderError) as e:
        run(_gemini("<html>oops</html>").complete(req("gemini:gemini-3.8-flash")))
    assert e.value.retryable


@pytest.mark.parametrize(
    ("body", "retryable"),
    [
        ('{"promptFeedback": {"blockReason": "SAFETY"}}', False),
        ('{"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}', False),
        ('{"candidates": [{"finishReason": "MALFORMED_FUNCTION_CALL"}]}', True),
    ],
)
def test_blocked_or_malformed_gemini_replies_raise(body, retryable):
    with pytest.raises(ProviderError) as e:
        run(_gemini(body).complete(req("gemini:gemini-3.8-flash")))
    assert e.value.retryable is retryable


def test_groq_reply_without_choices_raises():
    from griddesk.gateway.providers import GroqProvider

    p = GroqProvider("k", _FakeHTTP(200, '{"error": null}'))  # type: ignore[arg-type]
    with pytest.raises(ProviderError):
        run(p.complete(req("groq:qwen/qwen3.8-27b")))


def test_unsigned_tool_calls_get_the_placeholder_signature():
    msgs = [Message(role="assistant", tool_calls=[ToolCall(id="a", name="f", arguments={})])]
    _, contents = GeminiProvider.to_contents(msgs)
    assert contents[0]["parts"][0]["thoughtSignature"] == "skip_thought_signature_validator"
