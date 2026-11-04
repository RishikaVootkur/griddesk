"""Public list prices, used to report a list-price-equivalent cost even for free-tier calls.

Prices are USD per 1M tokens, checked 2026-11-03 at aistudio.google.com/docs/pricing (paid tier,
standard) and console.groq.com/docs/models. A local Ollama model has no list price, so it reports
$0 and is labeled self-hosted wherever costs appear.
"""

from __future__ import annotations

from dataclasses import dataclass

from griddesk.gateway.types import Usage

PRICES_CHECKED_ON = "2026-11-03"


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float
    note: str = ""


PRICES: dict[str, Price] = {
    "gemini:gemini-3.8-flash": Price(0.75, 3.75, "introductory price through 2026-12-31"),
    "gemini:gemini-3.6-flash": Price(0.75, 3.75, "introductory price through 2026-12-31"),
    "gemini:gemini-3.5-flash-lite": Price(0.30, 2.50),
    "groq:qwen/qwen3.8-27b": Price(0.80, 4.00),
    "groq:openai/gpt-oss-120b": Price(0.15, 0.60),
    "groq:openai/gpt-oss-20b": Price(0.075, 0.30),
    "ollama:qwen3:8b": Price(0.0, 0.0, "self-hosted, no list price"),
    "ollama:nomic-embed-text": Price(0.0, 0.0, "self-hosted, no list price"),
}


def list_price_cost(model: str, usage: Usage) -> float:
    """List-price-equivalent USD for one call. Unknown models cost 0 and are flagged by callers."""
    p = PRICES.get(model)
    if p is None:
        return 0.0
    return (usage.input_tokens * p.input_per_m + usage.output_tokens * p.output_per_m) / 1_000_000
