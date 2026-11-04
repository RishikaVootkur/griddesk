"""LLM gateway: provider fallback, retries, caching, budgets and list-price cost tracking."""

from __future__ import annotations

from pathlib import Path

import httpx

from griddesk.config import Settings, get_settings
from griddesk.gateway.cache import DiskCache
from griddesk.gateway.gateway import DEFAULT_CHAIN, CallRecord, Gateway, RateLimit
from griddesk.gateway.providers import GeminiProvider, GroqProvider, OllamaProvider, Provider
from griddesk.gateway.types import (
    BudgetExceeded,
    CacheMiss,
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "DEFAULT_CHAIN",
    "BudgetExceeded",
    "CacheMiss",
    "CallRecord",
    "Gateway",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "ProviderError",
    "RateLimit",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "build_gateway",
]


def build_gateway(
    settings: Settings | None = None, cache_dir: Path | None = None, cache_mode: str | None = None
) -> Gateway:
    """Create a gateway with every provider whose credentials are configured."""
    s = settings or get_settings()
    client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
    providers: dict[str, Provider] = {"ollama": OllamaProvider(s.ollama_base_url, client)}
    if s.gemini_api_key:
        providers["gemini"] = GeminiProvider(s.gemini_api_key.get_secret_value(), client)
    if s.groq_api_key:
        providers["groq"] = GroqProvider(s.groq_api_key.get_secret_value(), client)
    return Gateway(
        providers=providers,
        cache=DiskCache(cache_dir or s.cache_dir),
        cache_mode=cache_mode or s.cache_mode,
        max_retries=s.max_retries,
        conversation_budget=s.conversation_token_budget,
    )
