# GridDesk

**A customer-operations AI agent for a simulated electric utility, with the evaluation to prove it works.** Customers chat about bills, outages, payment plans, moving service and account changes. A triage agent hands each request to billing, outage or account specialists, which act through four MCP servers on a real Postgres database. They follow a 32-rule policy handbook that is enforced in code as well as in the prompt, pause for human approval on risky actions, redact personal data from every log and trace, and resist prompt injection. GridDesk is evaluated the way tau-bench evaluates agents: an LLM plays a customer with a hidden goal, and a trial passes only if the final database state matches the expected one. The suite has 86 tasks and 47 attacks, run on three models from three providers, with pass^k reliability, bootstrap confidence intervals and a CI regression gate that replays recorded runs without API keys.

Everything runs on free tiers: Gemini, Groq and a local Ollama model.

<!-- RESULTS:HEADLINE -->

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

## Results

<!-- RESULTS:TABLES -->

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
- **Read failures before tuning prompts.** Sorting every failed trial into a fixed taxonomy pointed at two concrete bugs that aggregate scores hid: the front desk promising a transfer it never made, and an outage rule the models read the wrong way. One was fixed in code and one in the prompt, each measured on the same tasks before and after (D14, D15).
- **Put the policy in the tools, then measure what the prompt alone does.** On the 47 attacks against qwen3:8b, switching off every defense (in-tool policy, action guard, spotlighting, injection filter) raised attack success from 12.8% to 27.7%. The prompt is a first line, and the code checks are what hold.
- **Free tiers shape the design.** Daily quotas, not speed, were the bottleneck: the 20-request daily cap on the newest Gemini Flash moved the agent to Flash-Lite, daily 429s are failed fast and never retried, and every response is cached by its full request so reruns, CI replays and the regression gate cost nothing.
- **Reproducible means pinned everywhere.** Byte-for-byte replay needed a fixed clock, deterministic ids, rolled-back trial transactions, and a cache key that does not depend on which API keys happen to be present.

