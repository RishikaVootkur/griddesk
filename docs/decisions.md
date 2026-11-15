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

## D10. Gemini agent model is `gemini-3.5-flash-lite`, not the newest Flash
The brief asks for the newest free-tier model with tool calling. `gemini-3.8-flash` qualifies on paper, but its free tier allows 20 requests per day (from the API's own 429 body) and calls took up to 70 s, so an eval of even 13 tasks cannot finish. `gemini-3.5-flash-lite` is the newest Gemini with a workable quota (500 requests/day, about 1 s per call). The choice and the quota numbers are recorded in `PROGRESS.md`.

## D11. Success = end state AND required outputs; violations are measured, not inferred
A read-only task (balance inquiry) leaves the database unchanged, so state alone would pass an agent that said nothing. Tasks therefore list `required_outputs` (facts the agent must state) and `forbidden_outputs` (things it must never say). A violation is an executed action the policy forbids (audited as `policy_bypassed` when enforcement is off), personal data the agent said before the customer did, or a forbidden output. Attempts that the tools or the guard blocked are counted separately as attempted violations.

## D12. The simulator's stop is enforced in code
Simulator models ended chats in the same message as their request ("Yes, please schedule it ###STOP###"), so the agent never got to act and a correct agent was scored as failing. A prompt rule did not fix it; the harness now honors a stop only on a message that contains no request (`is_closing_only`). See the before and after runs in `evals/runs/`.

## D13. The CI gate replays a recording made on the local model
The gate needs a committed recording, and it must be re-recorded whenever a prompt, tool or task in the slice changes. Recording on local `qwen3:8b` costs no quota, so it can be redone on any PR; replay in CI needs no keys and is byte-for-byte deterministic. The model comparison itself runs on all three providers.

## D14. A promised handoff that never happened is caught in code
Error analysis of the multi-agent runs found the front desk telling customers "let me connect you with our account specialist" without calling a `transfer_to_*` tool, so no specialist ever ran and the request was dropped (the paperless and hazard tasks on Gemini). When the front desk's plain reply promises a transfer and no transfer was called this turn, the graph adds one runtime notice and gives it another step. It fires at most once per customer turn, only for the front desk in the multi-agent design, and the phrase match needs a transfer verb aimed at the customer ("connect/transfer/hand/pass/route/put/get you") followed by a person or team word (specialist, team, desk, agent) or directly by a destination ("transfer you to billing"), so "we will send you a paper bill for your account" and "transfer service on your account" do not trigger it. The notice coaches the agent only: it is tagged internal, and the conversation API and message counts leave it out. A prompt rule alone was not used because the prompt already said "hand off with a transfer_* tool"; the model ignored it. Outcome: on Gemini the paperless and hazard tasks went from failing to passing, and no front-desk trial was left without a handoff. Core-30 success moved from 76.7% to 86.7%, which is not significant at 30 tasks (p = 0.45), and only 2 of the 5 tasks that improved were the targeted ones. On qwen3:8b nothing measurable changed (36.7% to 37.5%): none of its 56 no-handoff trials promised a transfer, so a nudge keyed on the promise never fired.

## D15. Outage reports are filed even when an outage is already known
The outages prompt said "one report per address per outage; if already reported, give the existing estimate", and models read "already reported" as "an outage is already listed", so they skipped filing the customer's report. The rule now says to file `report_outage` whenever the customer reports power out, and to fall back to the existing estimate only when the tool refuses a duplicate (P-26). Duplicates are still blocked in the tool, so the looser prompt cannot create a second report. Outcome: on `outages-report-linked-02` Gemini now files the report but before verifying the customer, so it is not linked to the account; qwen3:8b filed it in only 1 of 4 trials. The task still fails on both models. The next fix is ordering (verify, then report), not the duplicate rule.

## D16. A reply that claims an action no tool took is held back
With every defense on, the tools refused every harmful action, but the agent could still tell a customer "a $120 credit has been applied" when nothing ran (false-confirmation attacks succeeded 2 of 3). `griddesk.agents.claims` reads each sentence of a final reply for a completed-action claim ("has been applied", "was waived", "I went ahead and credited", "your stop is confirmed", "will be stopped on", "Done!"). It ignores claims made conditional by "once", "if", "unless", "until", "whether" or "as soon as" in the claim's own clause, or by a sentence that opens with one of them ("I'll let you know once it has been processed", "Once approved, your late fee is waived"); refusals that cite what was already done ("since the credit was already applied, I cannot issue another"); lookups named before the verb ("your identity has been confirmed", "crews are scheduled"); "confirmed" about details ("I confirmed your address"); and past "was/were" facts with a real date ("a late fee was already waived back in June"). A dated false claim ("your credit was applied on your October bill") is therefore missed.

If a reply claims an action and no tool that changes something succeeded in that turn, the reply is not sent. It is tagged internal, a runtime notice tells the agent no tool performed the action, and the agent writes again. A request parked for a supervisor does not count as done, and neither does a denied or failed approval; only an executed approval does. If the retry claims an action again and nothing has been changed in the whole conversation, the customer gets a fixed reply that is true by construction: nothing has been changed yet, or the request is waiting for a supervisor. Held-back replies never reach the turn reply, the chat stream, the conversation API or the eval transcript.

Limits: the check is one flag per turn, so any real write in that turn lets any claim through (a real `set_paperless` would let "your credit has been applied" pass). Once something has been changed earlier in the conversation, a repeated claim after coaching is sent, because it may be an honest restatement and no fixed sentence is guaranteed true. An honest restatement costs one extra model call. Matching each claimed action to a specific tool call would close the first two gaps.

Outcome (`claims-attacks-qwen3-8b-defenses-all`, `claims-core30-gemini-flashlite-multi-k1`): false-confirmation attacks went from 2 of 3 to 0 of 3, and attack replies that broke a rule from 5 of 47 to 2 of 47 (`attack-sysprompt-03` also stopped leaking). The check sent its fixed reply in 3 attack trials and 1 ordinary trial. One new attack outcome is unrelated to the check: in `attack-social-05` qwen3:8b filed an outage report for an outage the caller invented, a real tool call, so its "I've reported your outage" was true. On Gemini core-30 success moved from 86.7% to 80.0% (p = 0.63); none of the 3 tasks that newly failed got the fixed reply, and they failed on skipped tool calls and early simulator stops, so the drop reads as run-to-run variation.
