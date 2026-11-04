# Progress log

Resume here after a restart: read the latest entry, then `PLAN.md`.

## Status

- Done (merged): M0 setup (#1), M1 database (#2), M2 policy (#3), M4 gateway (#4), M3 billing MCP (#5), M5 agent (#6).
- In review: M6+M7 eval harness and CI gate (this PR), M13 web app (#7).
- Stacked and pushed, PR after the harness merges: `feat/specialist-agents` (M8 outages/accounts/knowledge servers, M9 triage + specialists), `feat/guardrails` (M10).
- In progress (subagent): `feat/task-suite` (80+ tasks, 42+ attacks).
- Next: M11 tracing, M12 API, experiments (M15), scheduled eval + Pages (M16), docs (M17).
- Blockers: Gemini free quotas (see below). Gemini resets at midnight Pacific.

## Model selection (2026-11-03, from each provider's model list API)

| Role | Provider | Model | Why |
|---|---|---|---|
| Agent | Gemini | `gemini-3.5-flash-lite` | Newest Gemini with a workable free quota (500 requests/day). `gemini-3.8-flash` is newer but capped at 20 requests/day and took 70 s per call; 3.6 Flash is reported at the same 20/day |
| Agent | Groq | `qwen/qwen3.8-27b` | Newest tool-calling model on Groq's free tier |
| Agent | Ollama | `qwen3:8b` | Local, required by the brief |
| User simulator | Groq | `openai/gpt-oss-120b` | Different family from all three agents |

Observed free-tier limits: Groq 1,000 requests/day and 8,000 tokens/minute per model. Gemini (from 429 bodies): `gemini-3.8-flash` 20 requests/day, `gemini-3.5-flash-lite` 500 requests/day; 3.x Flash models often return 503 "high demand".

## Key numbers

| Run | Model | Tasks x trials | Success | Violations | Notes |
|---|---|---|---|---|---|
| `slice-flashlite-k1-sim-v1` | gemini-3.5-flash-lite | 13 x 1 | 76.9% | 0% | 2 of 3 failures were simulator early stops |
| `slice-flashlite-k1-sim-v2` | gemini-3.5-flash-lite | 13 x 1 | 53.8% | 0% | Prompt-only stop rule; exposed relabeling and outage-id failures |
| CI gate baseline | qwen3:8b (local) | 13 x 1 | 30.8% | 0% | Replays deterministically from `evals/cache` |

## Log

- 2026-11-03: Repo created, scaffold on main. Probed models: Gemini 3.8/3.6/3.5-flash-lite support tools on free tier; `gemini-2.5-flash` is closed to new users; 3.1-pro has zero free quota.
- 2026-11-03 late: Eval harness end to end. Gemini flash-lite exhausted its 500/day quota during slice runs, so the CI gate baseline was recorded on local qwen3:8b (the gate only replays, so the model's quota does not matter in CI).
