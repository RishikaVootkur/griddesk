# Progress log

Resume here after a restart: read the latest entry, then `PLAN.md`.

## Status

- Done: M0 project setup (this PR).
- In progress: M1 database.
- Next: M2 policy, M3 billing MCP server, M4 gateway.
- Blockers: none.

## Model selection (2026-11-03, from each provider's model list API)

| Role | Provider | Model | Why |
|---|---|---|---|
| Agent | Gemini | `gemini-3.8-flash` | Newest free-tier Flash with function calling (`gemini-3.6-flash` as fallback when 3.8 is overloaded) |
| Agent | Groq | `qwen/qwen3.8-27b` | Newest tool-calling model on Groq's free tier |
| Agent | Ollama | `qwen3:8b` | Local, required by the brief |
| User simulator | Groq | `openai/gpt-oss-120b` | Different family from all three agents |

Observed free-tier limits: Groq 1,000 requests/day and 8,000 tokens/minute per model. Gemini returns frequent 503 "high demand" on 3.x Flash models.

## Key numbers

None yet.

## Log

- 2026-11-03: Repo created, scaffold on main. Probed models: Gemini 3.8/3.6/3.5-flash-lite support tools on free tier; `gemini-2.5-flash` is closed to new users; 3.1-pro has zero free quota.
