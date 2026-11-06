"""Provider adapters: Gemini (REST), Groq (OpenAI-compatible) and Ollama (native chat API).

Each adapter turns an `LLMRequest` into the provider's wire format, and the reply into an
`LLMResponse`. HTTP errors become `ProviderError`, marked retryable for rate limits, server
errors and timeouts. Adapters never log request bodies or headers, which carry keys and PII.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

import httpx

from griddesk.gateway.types import LLMRequest, LLMResponse, Message, ProviderError, ToolCall, Usage

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

# Settings that shape model output, per provider, folded into cache keys (D5). They are fixed strings,
# known even when a provider is not configured, so a keyless CI replay computes the same keys.
GEMINI_THINKING = "low"
OLLAMA_NUM_CTX = 16384
FINGERPRINTS = {
    "gemini": f"gemini/v1 thinking={GEMINI_THINKING}",
    "groq": "groq/v1 reasoning=qwen:none,gpt-oss:low",
    "ollama": f"ollama/v1 think=false num_ctx={OLLAMA_NUM_CTX}",
}
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


class Provider(Protocol):
    name: str

    async def complete(self, req: LLMRequest) -> LLMResponse: ...


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    m = re.match(r"^\s*([\d.]+)\s*(ms|s|m)?\s*$", value)
    if not m:
        return None
    n = float(m.group(1))
    return {"ms": n / 1000, "m": n * 60}.get(m.group(2) or "s", n)


def _tool_result_payload(content: str) -> Any:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    try:
        r = await client.post(url, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise ProviderError(f"transport error: {type(e).__name__}", retryable=True) from None
    if r.status_code != 200:
        retry_after = _parse_retry_after(r.headers.get("retry-after"))
        detail = r.text[:300]
        if retry_after is None:
            m = re.search(r'"retryDelay":\s*"([\d.]+s)"', r.text)
            retry_after = _parse_retry_after(m.group(1)) if m else None
        tool_use_failed = "tool_use_failed" in detail
        # A spent daily quota will not recover within any sensible backoff: fail fast and fall back.
        daily_quota = r.status_code == 429 and ("PerDay" in r.text or "per day" in r.text.lower())
        raise ProviderError(
            f"HTTP {r.status_code}: {detail}",
            status=r.status_code,
            retryable=(r.status_code in RETRYABLE_STATUS or tool_use_failed) and not daily_quota,
            retry_after=retry_after,
            # A malformed tool call usually repeats at temperature 0: one more try, then fall back.
            retry_limit=1 if tool_use_failed else None,
        )
    try:
        return r.json()
    except ValueError:
        raise ProviderError("HTTP 200 with a non-JSON body", status=200, retryable=True) from None


# Gemini 3 rejects a function call without a thought signature. Calls made by another model
# (after a fallback) have none, so they carry this documented placeholder instead.
SKIP_SIGNATURE = "skip_thought_signature_validator"
GEMINI_BAD_FINISH = {
    "SAFETY",
    "RECITATION",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "MALFORMED_FUNCTION_CALL",
    "UNEXPECTED_TOOL_CALL",
    "OTHER",
}


class GeminiProvider:
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, thinking_level: str = GEMINI_THINKING
    ) -> None:
        self._key = api_key
        self._client = client
        self._thinking = thinking_level
        self.fingerprint = f"gemini/v1 thinking={thinking_level}"

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def to_contents(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            parts: list[dict[str, Any]]
            if m.role == "user":
                role, parts = "user", [{"text": m.content}]
            elif m.role == "assistant":
                role, parts = "model", []
                if m.content:
                    part: dict[str, Any] = {"text": m.content}
                    if m.signature and not m.tool_calls:
                        part["thoughtSignature"] = m.signature
                    parts.append(part)
                for tc in m.tool_calls:
                    fc: dict[str, Any] = {
                        "functionCall": {"name": tc.name, "args": tc.arguments, "id": tc.id}
                    }
                    fc["thoughtSignature"] = tc.signature or SKIP_SIGNATURE
                    parts.append(fc)
                if not parts:
                    parts = [{"text": ""}]
            else:  # tool result
                role = "user"
                parts = [
                    {
                        "functionResponse": {
                            "name": m.name,
                            "id": m.tool_call_id,
                            "response": {"result": _tool_result_payload(m.content)},
                        }
                    }
                ]
            # Gemini wants alternating turns: merge consecutive same-role contents (parallel tool results).
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})
        return system, contents

    async def complete(self, req: LLMRequest) -> LLMResponse:
        system, contents = self.to_contents(req.messages)
        gen: dict[str, Any] = {"temperature": req.temperature, "maxOutputTokens": req.max_output_tokens}
        if req.model_name.startswith("gemini-3"):
            gen["thinkingConfig"] = {"thinkingLevel": self._thinking}
        if req.json_mode:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if req.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters}
                        for t in req.tools
                    ]
                }
            ]
        data = await _post(
            self._client, f"{self.BASE}/{req.model_name}:generateContent", body, {"x-goog-api-key": self._key}
        )
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError(f"Gemini returned no candidates ({reason})", status=200)
        finish = cands[0].get("finishReason", "STOP")
        if finish in GEMINI_BAD_FINISH:
            raise ProviderError(
                f"Gemini stopped with {finish}",
                status=200,
                retryable=finish == "MALFORMED_FUNCTION_CALL",
                retry_limit=1,
            )
        parts = (cands[0].get("content") or {}).get("parts", [])
        text, calls, sig = [], [], None
        for i, p in enumerate(parts):
            if p.get("thought"):
                continue
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(
                    ToolCall(
                        id=fc.get("id") or f"call_{i}",
                        name=fc["name"],
                        arguments=fc.get("args") or {},
                        signature=p.get("thoughtSignature"),
                    )
                )
            elif "text" in p:
                text.append(p["text"])
                sig = p.get("thoughtSignature") or sig
        um = data.get("usageMetadata", {})
        usage = Usage(
            input_tokens=um.get("promptTokenCount", 0),
            output_tokens=um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0),
        )
        return LLMResponse(
            model=req.model, content="".join(text), tool_calls=calls, signature=sig, usage=usage
        )


def _openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparsed": str(raw)}
    return val if isinstance(val, dict) else {"_unparsed": str(raw)}


class GroqProvider:
    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, client: httpx.AsyncClient) -> None:
        self._key = api_key
        self._client = client
        self.fingerprint = "groq/v1 reasoning=qwen:none,gpt-oss:low"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, req: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": req.model_name,
            "messages": _openai_messages(req.messages),
            "temperature": req.temperature,
            "max_completion_tokens": req.max_output_tokens,
        }
        if req.model_name.startswith("qwen/"):
            body["reasoning_effort"] = "none"
        elif req.model_name.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        if req.tools:
            body["tools"] = [{"type": "function", "function": t.model_dump()} for t in req.tools]
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        data = await _post(self._client, self.URL, body, {"Authorization": f"Bearer {self._key}"})
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices or "message" not in choices[0]:
            raise ProviderError("Groq reply has no choices", status=200, retryable=True, retry_limit=2)
        msg = choices[0]["message"]
        calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=_parse_arguments(tc["function"].get("arguments")),
            )
            for tc in msg.get("tool_calls") or []
        ]
        u = data.get("usage", {})
        return LLMResponse(
            model=req.model,
            content=_THINK.sub("", msg.get("content") or ""),
            tool_calls=calls,
            usage=Usage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0)),
        )


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, client: httpx.AsyncClient, num_ctx: int = OLLAMA_NUM_CTX) -> None:
        self._url = base_url.rstrip("/") + "/api/chat"
        self._client = client
        self._num_ctx = num_ctx
        self.fingerprint = f"ollama/v1 think=false num_ctx={num_ctx}"

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def to_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_name": m.name, "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content,
                        "tool_calls": [
                            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    async def complete(self, req: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": req.model_name,
            "messages": self.to_messages(req.messages),
            "stream": False,
            "think": False,
            "options": {
                "temperature": req.temperature,
                "num_predict": req.max_output_tokens,
                "num_ctx": self._num_ctx,
            },
        }
        if req.tools:
            body["tools"] = [{"type": "function", "function": t.model_dump()} for t in req.tools]
        if req.json_mode:
            body["format"] = "json"
        data = await _post(self._client, self._url, body, {})
        if not isinstance(data, dict) or "message" not in data:
            raise ProviderError("Ollama reply has no message", status=200, retryable=True, retry_limit=2)
        msg = data["message"]
        calls = [
            ToolCall(
                id=tc.get("id") or f"call_{i}",
                name=tc["function"]["name"],
                arguments=_parse_arguments(tc["function"].get("arguments")),
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
        ]
        return LLMResponse(
            model=req.model,
            content=_THINK.sub("", msg.get("content") or ""),
            tool_calls=calls,
            usage=Usage(
                input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0)
            ),
        )
