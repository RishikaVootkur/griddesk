# Design decisions

Each entry: the decision, the reason, and what it costs.

## D1. Python 3.12 with uv, strict mypy
The brief requires 3.12 through uv. Strict typing catches schema drift between tools, agents and the eval harness early.

## D2. Models chosen by listing each provider's API (2026-11-03)
Agents: `gemini-3.8-flash`, `qwen/qwen3.8-27b` (Groq), `qwen3:8b` (Ollama). User simulator: `openai/gpt-oss-120b` on Groq, a different model family from every agent under test, so the simulator does not share the agent's blind spots. See `PROGRESS.md` for the probe results.

## D3. Fixed simulated clock
The utility's "now" is fixed at 2026-10-15 15:00 UTC (`SIMULATED_NOW`). Seeds, policy windows ("once per 12 months") and cached LLM requests all depend on it, so a fixed clock is what makes runs replayable.

## D4. Own LLM gateway over raw HTTP, no vendor SDKs
Gemini, Groq and Ollama are called through small adapters (`griddesk.gateway.providers`) instead of three SDKs or LangChain chat models. One request/response type lets the gateway own fallback, retries, caching, budgets and cost in one place, and makes the cache key well defined. Cost: Gemini 3 quirks (thought signatures, `parametersJsonSchema`) are handled by hand.

## D5. Cache key = full request + trial namespace + provider fingerprint
Repeated eval trials must be independent samples, so the trial index is part of the key; a replay of the same trial must hit, so everything that shapes the request is part of the key. Adapter settings that change output (thinking level, reasoning effort, context size) go in through a provider fingerprint and a `CACHE_VERSION`.

## D6. Policy refusals are structured results, not exceptions
Tools return `ok=false` with `{code, rule_id, message}`. The agent can explain the refusal, and the eval counts refusals and violations by rule. Approvals are the same shape with `code=needs_approval` and an `approval_request_id`.

## D7. In-tool enforcement can be switched off, but is still audited
`ToolContext.enforce_policy=False` performs the action anyway and writes `outcome=policy_bypassed` with the broken rule id. The policy-violation rate is then measured the same way with and without enforcement, which is what the ablation needs.

## D8. The runtime owns `conversation_id` and approval execution
Every tool takes a `conversation_id` that the runtime overwrites and hides from the model, so verification state cannot be borrowed across conversations. `execute_approved_action` is tagged internal and hidden from the model; it acts only on a database row an operator approved, re-checks action and arguments, and cannot run twice.

## D9. Eval trials share one seeded database through rolled-back transactions
Each trial runs inside one connection and an outer transaction (`trial_scope`); tool commits become savepoints. Every trial starts from the identical seed with no re-seeding, and trials can run concurrently. New rows get ids from a per-trial seeded generator and timestamps from a ticking deterministic clock, so tool outputs, and therefore cached LLM requests, are identical on replay.
