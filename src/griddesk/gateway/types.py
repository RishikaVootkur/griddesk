"""Provider-neutral request and response types for the LLM gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
    # Gemini 3 requires its thought signature to be sent back with the function call on later turns.
    signature: str | None = None


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # for role == "tool"
    name: str | None = None  # tool name, for role == "tool"
    signature: str | None = None  # Gemini thought signature on a plain-text assistant turn


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class LLMRequest(BaseModel):
    model: str  # "<provider>:<model>", e.g. "gemini:gemini-3.8-flash"
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    temperature: float = 0.0
    max_output_tokens: int = 1024
    json_mode: bool = False

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def model_name(self) -> str:
        return self.model.split(":", 1)[1]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens, which providers bill as output

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(BaseModel):
    model: str  # the model that actually answered (may differ from the request after fallback)
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    signature: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0  # provider latency when first produced; replays keep the original value
    cost_usd: float = 0.0  # list-price equivalent
    cached: bool = False
    attempts: int = 1
    fallback_from: str | None = None

    def as_message(self) -> Message:
        return Message(
            role="assistant", content=self.content, tool_calls=self.tool_calls, signature=self.signature
        )


class ProviderError(Exception):
    """A provider call failed. `retryable` errors (429, 5xx, timeouts) are retried with backoff."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class BudgetExceeded(Exception):
    """The conversation used up its token budget."""


class CacheMiss(Exception):
    """Replay-only mode found no cached response for a request."""
