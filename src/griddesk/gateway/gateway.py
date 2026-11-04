"""The LLM gateway: the one path every model call in GridDesk takes.

Per call it (1) enforces the conversation's token budget, (2) serves from the disk cache when it
can, (3) waits on a client-side rate limiter sized to the provider's free tier, (4) retries
retryable errors with exponential backoff and jitter, honoring Retry-After, (5) falls back to the
next model in the chain when a model keeps failing, and (6) records tokens, latency and
list-price-equivalent cost for tracing and eval metrics.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from griddesk.gateway.cache import DiskCache, request_key
from griddesk.gateway.pricing import list_price_cost
from griddesk.gateway.providers import Provider
from griddesk.gateway.types import BudgetExceeded, CacheMiss, LLMRequest, LLMResponse, ProviderError

DEFAULT_CHAIN = ["gemini:gemini-3.8-flash", "groq:qwen/qwen3.8-27b", "ollama:qwen3:8b"]


@dataclass(frozen=True)
class RateLimit:
    rpm: int
    tpm: int | None = None
    concurrency: int = 8


# Sized a little under the observed free-tier limits (see PROGRESS.md).
DEFAULT_LIMITS: dict[str, RateLimit] = {
    "gemini": RateLimit(rpm=12, tpm=200_000, concurrency=4),
    "groq": RateLimit(rpm=28, tpm=7_500, concurrency=2),
    "ollama": RateLimit(rpm=1_000, concurrency=1),
}


@dataclass
class CallRecord:
    conversation_id: str | None
    requested_model: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    cached: bool
    attempts: int
    error: str | None = None


class _Limiter:
    """Sliding one-minute window over requests and tokens, plus a concurrency cap."""

    def __init__(
        self, limit: RateLimit, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]]
    ):
        self.limit = limit
        self._clock = clock
        self._sleep = sleep
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(limit.concurrency)

    async def acquire(self, tokens: int) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._events and now - self._events[0][0] >= 60:
                    self._events.popleft()
                used = sum(t for _, t in self._events)
                fits_tokens = self.limit.tpm is None or used + tokens <= self.limit.tpm or not self._events
                if len(self._events) < self.limit.rpm and fits_tokens:
                    self._events.append((now, tokens))
                    return
                await self._sleep(max(0.05, 60 - (now - self._events[0][0])))


def estimate_tokens(req: LLMRequest) -> int:
    chars = sum(len(m.content) + sum(len(str(tc.arguments)) for tc in m.tool_calls) for m in req.messages)
    chars += sum(len(t.description) + len(str(t.parameters)) for t in req.tools)
    return chars // 4 + req.max_output_tokens // 4


@dataclass
class Gateway:
    providers: dict[str, Provider]
    cache: DiskCache | None = None
    cache_mode: str = "read_write"  # off | read_write | replay_only
    max_retries: int = 6
    backoff_base: float = 2.0
    backoff_cap: float = 60.0
    conversation_budget: int = 400_000
    limits: dict[str, RateLimit] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    rng: random.Random = field(default_factory=random.Random)
    records: list[CallRecord] = field(default_factory=list)
    listeners: list[Callable[[CallRecord, LLMRequest, LLMResponse | None], None]] = field(
        default_factory=list
    )
    _used: dict[str, int] = field(default_factory=dict)
    _limiters: dict[str, _Limiter] = field(default_factory=dict)

    def tokens_used(self, conversation_id: str) -> int:
        return self._used.get(conversation_id, 0)

    def _limiter(self, provider: str) -> _Limiter:
        if provider not in self._limiters:
            self._limiters[provider] = _Limiter(
                self.limits.get(provider, RateLimit(rpm=60)), self.clock, self.sleep
            )
        return self._limiters[provider]

    async def complete(
        self,
        req: LLMRequest,
        *,
        conversation_id: str | None = None,
        namespace: str = "",
        fallbacks: list[str] | None = None,
    ) -> LLMResponse:
        """Answer `req`, trying `req.model` then each model in `fallbacks`."""
        if conversation_id and self.tokens_used(conversation_id) >= self.conversation_budget:
            raise BudgetExceeded(
                f"conversation used {self.tokens_used(conversation_id)} of {self.conversation_budget} tokens"
            )
        chain = [req.model, *(fallbacks or [])]
        last_error: Exception | None = None
        for model in chain:
            attempt_req = req if model == req.model else req.model_copy(update={"model": model})
            try:
                resp = await self._complete_one(attempt_req, namespace)
            except CacheMiss:
                raise
            except ProviderError as e:
                last_error = e
                self._record(conversation_id, req.model, attempt_req, None, error=str(e)[:200])
                continue
            if model != req.model:
                resp = resp.model_copy(update={"fallback_from": req.model})
            if conversation_id:
                self._used[conversation_id] = self.tokens_used(conversation_id) + resp.usage.total
            self._record(conversation_id, req.model, attempt_req, resp)
            return resp
        assert last_error is not None
        raise last_error

    async def _complete_one(self, req: LLMRequest, namespace: str) -> LLMResponse:
        key = request_key(req, namespace)
        if self.cache and self.cache_mode != "off":
            hit = self.cache.get(key)
            if hit is not None:
                return hit.model_copy(update={"cached": True})
            if self.cache_mode == "replay_only":
                raise CacheMiss(f"no cached response for {req.model} (key {key[:12]})")
        provider = self.providers.get(req.provider)
        if provider is None:
            raise ProviderError(f"provider {req.provider!r} is not configured")
        limiter = self._limiter(req.provider)
        attempt = 0
        while True:
            attempt += 1
            await limiter.acquire(estimate_tokens(req))
            t0 = self.clock()
            try:
                async with limiter.sem:
                    resp = await provider.complete(req)
            except ProviderError as e:
                if not e.retryable or attempt > self.max_retries:
                    raise
                delay = min(self.backoff_cap, self.backoff_base * 2 ** (attempt - 1)) * self.rng.uniform(
                    0.5, 1.0
                )
                if e.retry_after is not None:
                    delay = max(delay, min(e.retry_after + 0.5, self.backoff_cap * 2))
                await self.sleep(delay)
                continue
            resp = resp.model_copy(
                update={
                    "latency_ms": round((self.clock() - t0) * 1000, 1),
                    "cost_usd": list_price_cost(req.model, resp.usage),
                    "attempts": attempt,
                }
            )
            if self.cache and self.cache_mode == "read_write":
                self.cache.put(key, resp)
            return resp

    def _record(
        self,
        conversation_id: str | None,
        requested: str,
        req: LLMRequest,
        resp: LLMResponse | None,
        error: str | None = None,
    ) -> None:
        rec = CallRecord(
            conversation_id=conversation_id,
            requested_model=requested,
            model=req.model,
            input_tokens=resp.usage.input_tokens if resp else 0,
            output_tokens=resp.usage.output_tokens if resp else 0,
            latency_ms=resp.latency_ms if resp else 0.0,
            cost_usd=resp.cost_usd if resp else 0.0,
            cached=resp.cached if resp else False,
            attempts=resp.attempts if resp else 0,
            error=error,
        )
        self.records.append(rec)
        for fn in self.listeners:
            fn(rec, req, resp)
