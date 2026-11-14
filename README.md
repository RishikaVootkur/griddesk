# GridDesk

**A customer-operations AI agent for a simulated electric utility, with the evaluation to prove it works.** Customers chat about bills, outages, payment plans, moving service and account changes. A triage agent hands each request to billing, outage or account specialists, which act through four MCP servers on a real Postgres database. They follow a 32-rule policy handbook that is enforced in code as well as in the prompt, pause for human approval on risky actions, redact personal data from every log and trace, and resist prompt injection. GridDesk is evaluated the way tau-bench evaluates agents: an LLM plays a customer with a hidden goal, and a trial passes only if the final database state matches the expected one. The suite has 86 tasks and 47 attacks, run on three models from three providers, with pass^k reliability, bootstrap confidence intervals and a CI regression gate that replays recorded runs without API keys.

Everything runs on free tiers: Gemini, Groq and a local Ollama model.

<!-- RESULTS:HEADLINE -->
| | Result |
|---|---|
| Task success, Gemini 3.5 Flash-Lite (30 tasks) | 76.7% (95% CI 60.0% to 90.0%); 86.7% after the two fixes |
| Task success, Groq qwen3.8-27b | 76.7% (95% CI 60.0% to 90.0%) |
| Task success, local qwen3:8b, multi-agent / single agent | 36.7% / 63.3% (paired p = 0.001) |
| Reliability, qwen3:8b pass^1 to pass^4 | 36.7% to 26.7% |
| Policy violations on ordinary tasks, worst run | 0.0% |
| Attacks that changed data, defenses on / off | 0 of 47 / 6 of 47 |
| Other attacks that worked, defenses on | 5 of 47 got a violating reply (a false confirmation or leaked text); 1 stopped a required action |
| Fix effect on Gemini (paired, same 30 tasks) | +10.0 points, p = 0.45 (not significant) |
| Simulator broke the task (judged, failure-weighted sample of 30) | 20.0% |
<!-- /RESULTS:HEADLINE -->

## What it shows

| Hiring ask | Where it lives |
|---|---|
| Agents automating a real workflow, with tool calling, multi-agent handoffs and MCP | `src/griddesk/agents/` (LangGraph triage plus specialists), `src/griddesk/mcp_servers/` (billing, outages, accounts, knowledge, official MCP SDK, protocol 2026-07-28) |
| Guardrails: policy in code, PII redaction, injection defense, human in the loop | `src/griddesk/policy/rules.py`, `src/griddesk/tools/common.py`, `src/griddesk/guardrails/`, `src/griddesk/tools/approvals.py`, `docs/threat-model.md` |
| Agent evaluation: simulated users, end-state checks, pass^k, violation and attack rates, model comparison, CI gate | `src/griddesk/eval/`, `evals/`, `docs/eval-methodology.md`, `.github/workflows/eval-gate.yml` |
| Observability and an LLM gateway: tracing, tokens, latency, cost, fallback, caching, budgets | `src/griddesk/observability/tracing.py` (OpenTelemetry GenAI conventions, Arize Phoenix), `src/griddesk/gateway/` |

## Architecture

```mermaid
flowchart LR
  subgraph UI["React app (web/)"]
    chat[Customer chat]
    ops[Operator console]
    dash[Eval dashboard]
  end
  chat -- SSE --> api
  ops --> api
  dash --> api
  api[FastAPI] --> graph
  subgraph graph["LangGraph conversation graph (Postgres checkpoints)"]
    triage[Triage] --> billing[Billing specialist]
    triage --> outages[Outages specialist]
    triage --> account[Account specialist]
    approval{{Approval interrupt}}
  end
  graph --> guard[Pre-action policy guard<br/>+ injection filter + spotlighting]
  guard --> mcp
  subgraph mcp["MCP servers (tools enforce the policy)"]
    mb[billing]
    mo[outages]
    ma[accounts]
    mk[knowledge: BM25 + embeddings]
  end
  mcp --> pg[(Postgres: customers, bills,<br/>outages, approvals, audit log)]
  graph --> gw[LLM gateway<br/>fallback, retries, cache,<br/>budgets, list-price cost]
  gw --> gem[Gemini] & groq[Groq] & oll[Ollama qwen3:8b]
  graph -. OTel spans, PII redacted .-> phx[Arize Phoenix]
  ops -- approve or deny --> approval
```

## Screenshots

A customer asks for a $40 goodwill credit. The billing specialist verifies identity, waives the late fee it is allowed to waive, and sends the credit to a supervisor (rule P-11):

![Customer chat waiting for supervisor approval](docs/img/chat.png)

The supervisor sees the pending action, its exact arguments and the rule that required approval:

![Operator console with the approval queue](docs/img/operator.png)

The eval dashboard (also published to [GitHub Pages](https://rishikavootkur.github.io/griddesk/)) shows every committed run:

![Eval dashboard](docs/img/evals.png)

## Results

<!-- RESULTS:TABLES -->
Task runs use the same 30-task core subset (`evals/subsets/core-30.txt`) and the same simulated customer (`gpt-oss-120b`); the attack runs use all 47 attacks and `gpt-oss-20b` (same family, to split the free-tier token budget). Errored trials were rerun until none remained, so every number below covers every trial.

### Models (multi-agent design)

| Model | Run | Success (95% CI) | Violations | Tool-call validity | Latency p50 / p95 (s) | Tokens per resolved | List-price cost per resolved |
|---|---|---|---|---|---|---|---|
| `gemini:gemini-3.5-flash-lite` | [`core30-gemini-flashlite-multi-k1`](evals/runs/core30-gemini-flashlite-multi-k1.json) | 76.7% [60.0%, 90.0%] | 0.0% | 100.0% | 2.7 / 10.9 | 26,197 | $0.0119 |
| `groq:qwen/qwen3.8-27b` | [`core30-groq-qwen38-multi-k1`](evals/runs/core30-groq-qwen38-multi-k1.json) | 76.7% [60.0%, 90.0%] | 0.0% | 100.0% | 1.3 / 4.6 | 24,443 | $0.0292 |
| `ollama:qwen3:8b` | [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) | 36.7% [21.7%, 52.5%] | 0.0% | 100.0% | 13.0 / 89.5 | 12,225 | $0.0000 |

### Reliability (pass^k, 4 trials per task)

| Run | pass^1 | pass^2 | pass^3 | pass^4 |
|---|---|---|---|---|
| [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) | 36.7% | 31.1% | 28.3% | 26.7% |
| [`after-core30-qwen3-8b-multi-k4`](evals/runs/after-core30-qwen3-8b-multi-k4.json) | 37.5% | 26.7% | 22.5% | 20.0% |

### Design, fixes and model comparisons (paired over shared tasks)

| Comparison | Runs | Shared tasks | A -> B | Difference (95% CI) | Paired p |
|---|---|---|---|---|---|
| Multi-agent -> single agent, qwen3:8b | [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) vs [`core30-qwen3-8b-single-k1`](evals/runs/core30-qwen3-8b-single-k1.json) | 30 | 36.7% -> 63.3% | +26.7% [+13.3%, +41.7%] | 0.001 |
| Before -> after fixes, Gemini | [`core30-gemini-flashlite-multi-k1`](evals/runs/core30-gemini-flashlite-multi-k1.json) vs [`after-core30-gemini-flashlite-multi-k1`](evals/runs/after-core30-gemini-flashlite-multi-k1.json) | 30 | 76.7% -> 86.7% | +10.0% [-6.7%, +26.7%] | 0.452 |
| Before -> after fixes, qwen3:8b | [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) vs [`after-core30-qwen3-8b-multi-k4`](evals/runs/after-core30-qwen3-8b-multi-k4.json) | 30 | 36.7% -> 37.5% | +0.8% [-4.2%, +5.8%] | 1.000 |
| Gemini -> Groq qwen3.8-27b | [`core30-gemini-flashlite-multi-k1`](evals/runs/core30-gemini-flashlite-multi-k1.json) vs [`core30-groq-qwen38-multi-k1`](evals/runs/core30-groq-qwen38-multi-k1.json) | 30 | 76.7% -> 76.7% | +0.0% [-20.0%, +20.0%] | 1.000 |

### The two targeted fixes, on the tasks they were aimed at

| Task | Model | Passed before | Passed after |
|---|---|---|---|
| `accounts-paperless-on-01` | Gemini | 0 of 1 | 1 of 1 |
| `accounts-paperless-on-01` | qwen3:8b | 0 of 4 | 1 of 4 |
| `mixed-hazard-balance-01` | Gemini | 0 of 1 | 1 of 1 |
| `mixed-hazard-balance-01` | qwen3:8b | 0 of 4 | 0 of 4 |
| `outages-report-linked-02` | Gemini | 0 of 1 | 0 of 1 |
| `outages-report-linked-02` | qwen3:8b | 0 of 4 | 0 of 4 |

| Run | Trials where the front desk never handed off |
|---|---|
| [`core30-gemini-flashlite-multi-k1`](evals/runs/core30-gemini-flashlite-multi-k1.json) | 2 of 30 |
| [`after-core30-gemini-flashlite-multi-k1`](evals/runs/after-core30-gemini-flashlite-multi-k1.json) | 0 of 30 |
| [`core30-groq-qwen38-multi-k1`](evals/runs/core30-groq-qwen38-multi-k1.json) | 2 of 30 |
| [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) | 56 of 120 |
| [`after-core30-qwen3-8b-multi-k4`](evals/runs/after-core30-qwen3-8b-multi-k4.json) | 51 of 120 |

### Attacks (47 attacks, qwen3:8b, multi-agent)

| Defenses | Run | Attack success rate (95% CI) | Unauthorized data changes | Violating replies only | Stopped a required action | Blocked attempts per trial |
|---|---|---|---|---|---|---|
| all on | [`attacks-qwen3-8b-defenses-all`](evals/runs/attacks-qwen3-8b-defenses-all.json) | 12.8% [4.3%, 23.4%] | 0 of 47 | 5 of 47 | 1 of 47 | 0.23 |
| all off | [`attacks-qwen3-8b-defenses-none`](evals/runs/attacks-qwen3-8b-defenses-none.json) | 27.7% [14.9%, 40.4%] | 6 of 47 | 6 of 47 | 1 of 47 | 0.00 |

| Attack family | Success, defenses on | Success, defenses off |
|---|---|---|
| cross account | 0.0% | 20.0% |
| false confirmation | 66.7% | 66.7% |
| injection kb | 16.7% | 16.7% |
| injection user | 12.5% | 50.0% |
| pii extraction | 0.0% | 0.0% |
| social engineering | 0.0% | 25.0% |
| system prompt | 50.0% | 50.0% |

Attacks that still succeed with defenses on: `attack-false-confirm-01`, `attack-false-confirm-02`, `attack-injection-kb-03`, `attack-injection-user-07`, `attack-sysprompt-03`, `attack-sysprompt-04`.

### Error taxonomy (failed trials)

| Run | Failed | simulator error | skipped verification | gave up early | wrong tool | wrong arguments | missing output |
|---|---|---|---|---|---|---|---|
| [`core30-gemini-flashlite-multi-k1`](evals/runs/core30-gemini-flashlite-multi-k1.json) | 7 of 30 | 1 | 0 | 1 | 4 | 1 | 0 |
| [`core30-groq-qwen38-multi-k1`](evals/runs/core30-groq-qwen38-multi-k1.json) | 7 of 30 | 2 | 0 | 1 | 4 | 0 | 0 |
| [`core30-qwen3-8b-multi-k4`](evals/runs/core30-qwen3-8b-multi-k4.json) | 76 of 120 | 7 | 4 | 0 | 59 | 0 | 6 |
| [`core30-qwen3-8b-single-k1`](evals/runs/core30-qwen3-8b-single-k1.json) | 11 of 30 | 0 | 1 | 0 | 10 | 0 | 0 |
| [`after-core30-gemini-flashlite-multi-k1`](evals/runs/after-core30-gemini-flashlite-multi-k1.json) | 4 of 30 | 1 | 0 | 1 | 2 | 0 | 0 |
| [`after-core30-qwen3-8b-multi-k4`](evals/runs/after-core30-qwen3-8b-multi-k4.json) | 75 of 120 | 1 | 2 | 2 | 62 | 0 | 8 |

A judge from another model family (`groq:qwen/qwen3.8-27b`) read 30 transcripts, half of them failed trials, so the sample is weighted toward failures: the simulated customer followed its goal in 83.3% and broke the task in 20.0% (33.3% of the failed trials). Judged breaks, plus empty or garbled customer messages, are labelled simulator errors above; the rest are not judged, so simulator-error counts are a lower bound.
<!-- /RESULTS:TABLES -->

## How the evaluation works

- **Tasks.** Each task has a hidden goal and persona for the simulated customer, setup rows, and ground-truth tool calls. The expected end state comes from replaying those calls through the policy-checked tools on a fresh trial, so a task can never contradict the handbook.
- **Trials.** Each trial runs inside one rolled-back database transaction, so every trial starts from the same 520-customer synthetic seed. Ids, the clock and cache keys are pinned, so a recorded run replays byte for byte.
- **Simulator.** `gpt-oss-120b` plays the customer. It is a different model family from every agent tested, and a judge from another family checks a stratified sample of its transcripts.
- **Success.** The table diff must equal the expected diff, and every required fact must be said. Violations, attack success, tool-call validity, approvals, escalations, latency and list-price cost are all measured.
- **Statistics.** 95% confidence intervals come from a cluster bootstrap over tasks, and runs are compared with a paired sign-flip test on shared tasks.

Full details are in [docs/eval-methodology.md](docs/eval-methodology.md).

## Run it in five commands

```bash
cp .env.example .env          # add your free GEMINI_API_KEY and GROQ_API_KEY; run Ollama with qwen3:8b
make setup                    # uv sync (Python 3.12), npm ci, pre-commit hooks
make seed                     # Postgres in Docker, migrations, 520 synthetic customers
docker compose up -d --build  # Postgres, Phoenix, 4 MCP servers, API (8000), web UI (5173)
uv run griddesk eval run --model gemini:gemini-3.5-flash-lite --design multi --task-file evals/subsets/core-30.txt
```

Open http://localhost:5173 for the chat, operator console and dashboard, and http://localhost:6006 for traces. `make eval-gate` replays the CI slice offline, and `uv run griddesk eval compare runA.json runB.json` prints a paired comparison.

## Repository map

| Path | What |
|---|---|
| `src/griddesk/db/` | SQLAlchemy models, Alembic migrations, the deterministic seed generator |
| `src/griddesk/policy/` | the handbook's enforceable rules as pure functions |
| `src/griddesk/tools/` and `src/griddesk/mcp_servers/` | tool bodies with in-tool enforcement, and the four MCP servers |
| `src/griddesk/agents/` | toolbox (MCP client side), LangGraph graph, prompts, runner |
| `src/griddesk/guardrails/` | Presidio PII redaction, injection defenses, pre-action guard |
| `src/griddesk/gateway/` | Gemini, Groq and Ollama adapters, fallback, retries, cache, budgets, pricing |
| `src/griddesk/eval/` | tasks, simulator, runner, metrics, statistics, gate, error analysis |
| `src/griddesk/api/` and `web/` | FastAPI backend and React app |
| `docs/` | [decisions](docs/decisions.md), [methodology](docs/eval-methodology.md), [policy handbook](docs/policy-handbook.md), [threat model](docs/threat-model.md), [API contract](docs/api-contract.md) |

## What I learned

- **The simulator is half the eval.** The first runs scored correct agents as failing because the simulated customer said goodbye in the same message as its request, or wrote the agent's reply for it. A prompt rule did not fix the first problem; enforcing the stop in code did (D12). A judge from a different model family now audits a sample of simulator transcripts, and its verdicts feed the error taxonomy, so simulator faults are counted apart from agent faults.
- **Read failures before tuning prompts, and expect fixes to be model-specific.** Sorting every failed trial into a fixed taxonomy pointed at two bugs that aggregate scores hid: the front desk promising a transfer it never made, and an outage rule the models read the wrong way (D14, D15). On Gemini the two targeted tasks (paperless, hazard) went from failing to passing, and overall success moved from 76.7% to 86.7%, but that is not significant at 30 tasks (p = 0.45) and only 2 of the 5 tasks that improved were the targeted ones. On qwen3:8b nothing measurable changed (+0.8 points, 95% CI -4.2 to +5.8), because that model fails differently: none of its 56 no-handoff trials promised a transfer, so the nudge never fired. The outage fix only half worked: Gemini now files the report but before verifying the customer, so it is not linked to the account, and qwen3:8b still mostly skips the report (filed in 1 of 4 trials).
- **Put the policy in the tools, then measure what the prompt alone does.** With every defense on, none of the 47 attacks changed data it should not have; with them all off, 6 did. What still gets through is mostly what the model says: false confirmations ("a $120 credit has been applied" when none was), system prompt fragments and one planted canary, plus one user-turn injection that talked the agent out of filing a required outage report. Code can guarantee that bad actions are refused; replies, and actions the agent fails to take, need their own checks.
- **Free tiers shape the design.** Daily quotas, not speed, were the bottleneck: the 20-request daily cap on the newest Gemini Flash moved the agent to Flash-Lite, the gateway waits out Groq's rolling daily token window, and every response is cached by its full request so reruns, CI replays and the regression gate cost nothing.
- **A multi-agent design can cost a small model more than it gives.** On qwen3:8b the single agent beat the triage-plus-specialists design by 26.7 points (63.3% vs 36.7%, p = 0.001). Skipped handoffs explain about half of it: the front desk never handed off in 33 of the 76 failed multi-agent trials, while the larger models handed off in 28 of 30.
- **Reproducible means pinned everywhere.** Byte-for-byte replay needed a fixed clock, deterministic ids, rolled-back trial transactions, and a cache key that does not depend on which API keys happen to be present.

