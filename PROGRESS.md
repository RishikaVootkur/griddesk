# Progress log

Resume here after a restart: read the latest entry, then `PLAN.md`.

## Status

- Merged: #1 setup, #2 database, #3 policy, #4 gateway, #5 billing MCP, #6 agent, #7 web app, #8 eval harness and CI gate.
- In review (stacked): #9 specialist agents (base main), #10 task suite (base #9), #11 guardrails (base #9), #12 API, tracing and compose (base #11).
- Experiments run from a local integration worktree (`.griddesk/worktrees/exp`, branch `exp/integration` = main + all stacked branches + experiment tooling). Its extra commits (analysis, results tables, docs drafts, live-eval and Pages workflows) become the final PRs after the stack merges.
- Repo secret `GEMINI_API_KEY` set; GitHub Pages enabled (workflow mode): https://rishikavootkur.github.io/griddesk/
- Running: core-30 model comparison (Gemini flash-lite, Groq qwen3.8-27b), core-30 x 4 on local qwen3:8b (pass^k), attack suite with all defenses (local).
- Next: attack ablations (none, in-tool only, in-tool + guard), single vs multi, simulator validation, error analysis and two fixes, README, resume bullets.
- Bottleneck: the simulator's Groq quota (8k tokens per minute per model) caps throughput at about two trials a minute across all runs.

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
- 2026-11-07: Groq's 200k tokens/day simulator cap stopped the experiment runs at about 11:30 (every later trial got a 429). PR #17: the gateway can wait out Groq's rolling quota (`QUOTA_WAIT_SECONDS`), so long runs pace themselves.
- 2026-11-08: Three runs hung for up to 12 hours: concurrent trials insert the same deterministic ids, and a blocked synchronous insert froze its own event loop. Restarted at concurrency 1; PR #18 adds a trial lock timeout, surfaces it through MCP, retries blocked trials.
