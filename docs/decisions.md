# Design decisions

Each entry: the decision, the reason, and what it costs.

## D1. Python 3.12 with uv, strict mypy
The brief requires 3.12 through uv. Strict typing catches schema drift between tools, agents and the eval harness early.

## D2. Models chosen by listing each provider's API (2026-11-03)
Agents: `gemini-3.8-flash`, `qwen/qwen3.8-27b` (Groq), `qwen3:8b` (Ollama). User simulator: `openai/gpt-oss-120b` on Groq, a different model family from every agent under test, so the simulator does not share the agent's blind spots. See `PROGRESS.md` for the probe results.

## D3. Fixed simulated clock
The utility's "now" is fixed at 2026-10-15 15:00 UTC (`SIMULATED_NOW`). Seeds, policy windows ("once per 12 months") and cached LLM requests all depend on it, so a fixed clock is what makes runs replayable.
