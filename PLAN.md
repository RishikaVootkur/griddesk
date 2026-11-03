# GridDesk build plan

Each milestone lands as one or more pull requests. A milestone is done when its acceptance criteria hold on `main` and CI is green.

## Phase 1: thin end-to-end slice

**M0. Project setup**
- uv project on Python 3.12, ruff, mypy (strict), pytest, pre-commit with gitleaks.
- CI runs lint, types, tests (with a Postgres service) and gitleaks on every PR.
- Typed settings; keys only in `.env`; `.env.example` committed.

**M1. Simulated utility database**
- PostgreSQL 17 in Docker Compose; SQLAlchemy 2 models; Alembic migration for every table.
- Tables: customers, service_addresses, accounts, meters, meter_readings, bills, bill_line_items, payments, payment_plans, outages, service_orders, identity_verifications, approval_requests, escalations, audit_log.
- Deterministic seed generator (`make seed`): 500+ synthetic customers with edge cases (estimated readings, disputed bills, past-due, medical necessity, prior plan defaults, recent late-fee waivers).
- Tests check row counts, edge-case coverage and determinism.

**M2. Policy handbook and engine**
- `docs/policy-handbook.md` with 25+ numbered rules (P-01...).
- `griddesk.policy` implements every enforceable rule as a pure function returning allow / deny / needs-approval with the rule id.
- Unit tests per rule, including boundary values.

**M3. Billing MCP server**
- MCP server (official SDK, protocol 2026-07-28) with Pydantic input and output schemas.
- Tools refuse disallowed actions with a structured error carrying the rule id. In-tool enforcement can be switched off for the ablation.
- Tests drive the server through an in-process MCP client.

**M4. LLM gateway**
- One interface for Gemini, Groq and Ollama with native tool calling.
- Fallback chain, retries with exponential backoff and jitter on 429/5xx, disk cache keyed on the full request, per-conversation token budget, list-price-equivalent cost tracking.
- Unit tests with fake providers: fallback order, retry, cache hit, budget exhaustion, cost math.

**M5. Billing agent**
- LangGraph agent loop that calls MCP tools through the gateway; the runtime injects the conversation id, so the model cannot act as another session.

**M6. Evaluation harness (slice)**
- Task schema, 10 billing tasks, user simulator on a different model family, end-state comparison against a replay of the expected actions, run files, `griddesk eval run` and `griddesk eval compare`.
- Metric functions (success, pass^k, rates, percentiles, bootstrap CI, paired test) tested against hand-computed values.

**M7. CI regression gate**
- Workflow replays a fixed task slice from the committed response cache with no keys, fails past thresholds and posts a metric-diff comment.

## Phase 2: depth

**M8. Remaining MCP servers**: outages, accounts (identity verification, contact updates, start/stop/transfer service), knowledge (hybrid BM25 plus embedding retrieval over the handbook, fused with reciprocal rank).

**M9. Multi-agent graph**: triage routes to billing, outages and account specialists with handoff tools and shared state; single-agent variant for comparison; Postgres checkpointing (conversation survives a restart); human-in-the-loop approval with `interrupt()` and resume.

**M10. Guardrails**: Presidio PII redaction on logs and traces; injection defenses (spotlighting of untrusted text, tool-output quarantine, detector); pre-action policy check on every write; adversarial test suite in pytest.

**M11. Observability**: OpenTelemetry spans with GenAI semantic conventions (agent turn, handoff, tool call, guardrail decision, LLM call with tokens, latency and cost) exported to Arize Phoenix; PII redacted before export.

**M12. API**: FastAPI with streamed (SSE) chat, conversation list, approval queue (approve/deny resumes the graph), audit log, eval results.

**M13. Web app**: React + TypeScript + Vite + Tailwind: customer chat, operator console, eval dashboard. Static dashboard build published to GitHub Pages.

**M14. Full suites**: 80+ tasks across billing, outages, accounts and mixed requests at three difficulty levels, including refuse and escalate tasks; 40+ adversarial attacks.

**M15. Experiments**
- Three models (Gemini, Groq, local qwen3:8b) on success, pass^k, latency, cost.
- Single agent vs triage plus specialists; with vs without in-tool policy enforcement; attack success before and after each defense.
- Simulator validation on 30 sampled transcripts.
- Error taxonomy with counts and examples; at least two targeted fixes with before and after numbers.
- Bootstrap 95% CIs and paired tests on the same tasks.

**M16. Scheduled live eval** on the `GEMINI_API_KEY` secret; Pages deploy of the dashboard from the latest results.

**M17. Docs**: README for recruiters, `docs/decisions.md`, `docs/eval-methodology.md`, `docs/policy-handbook.md`, `docs/threat-model.md`, `RESUME_BULLETS.md`.

## Budget notes

Free tiers are the binding constraint. Groq allows 1,000 requests per day and 8,000 tokens per minute per model; Gemini's free quotas are per project and not published per model. Large runs therefore lean on local Ollama and the response cache, and every run file records how many calls were served from the cache.
