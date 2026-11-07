"""Error analysis and simulator validation over run files.

`classify` sorts every failed trial into one taxonomy bucket (first match wins, in this order):

1. simulator_error   the simulated customer broke the task (judge verdict, empty message, garbled text)
2. infra_error       provider, budget or cache failure: not the agent's fault
3. policy_breach     a violation happened (forbidden action executed, PII leaked, forbidden output said)
4. skipped_verification  the agent tried an account action before verifying identity (P-01 refusal)
5. wrong_tool        an expected tool was never called, or an unrequested write changed the database
6. wrong_arguments   the right tools were called but the resulting rows differ (amount, date, id...)
7. gave_up_early     the agent escalated or stopped without doing the expected work
8. missing_output    the end state was right but a required fact was never said

`validate_simulator` asks a judge model from a third family to read sampled transcripts against
their hidden goals and say whether the simulated customer followed the goal or broke the task.
"""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from typing import Any

from griddesk.eval.tasks import Task
from griddesk.gateway import Gateway, LLMRequest, Message

TAXONOMY = [
    "simulator_error",
    "infra_error",
    "policy_breach",
    "skipped_verification",
    "wrong_tool",
    "wrong_arguments",
    "gave_up_early",
    "missing_output",
]
WRITE_TABLES = {
    "credits",
    "payment_plans",
    "service_orders",
    "outage_reports",
    "escalations",
    "customers",
    "accounts",
    "bills",
}


def _garbled(trial: dict[str, Any]) -> bool:
    for m in trial["transcript"]:
        if m["speaker"] != "customer":
            continue
        text = m["text"]
        if re.search(r"(.{12,}?)\1{2,}", text) or "We need to respond" in text:
            return True
    return False


def classify(trial: dict[str, Any], task: Task, sim_verdict: dict[str, Any] | None = None) -> str | None:
    if trial["success"]:
        return None
    err = trial.get("error") or ""
    if (sim_verdict and sim_verdict.get("broke_task")) or err.startswith("SimulatorError") or _garbled(trial):
        return "simulator_error"
    if err:
        return "infra_error"
    if trial["violation"]:
        return "policy_breach"
    called = [c["tool"] for c in trial["tool_calls"]]
    verified_at = next(
        (i for i, c in enumerate(trial["tool_calls"]) if c["tool"] == "verify_identity" and c["ok"]), None
    )
    early = [
        c
        for i, c in enumerate(trial["tool_calls"])
        if c.get("rule_id") == "P-01" and (verified_at is None or i < verified_at)
    ]
    needs_verify = any(a.tool == "verify_identity" for a in task.expected_actions)
    if early and needs_verify and verified_at is None:
        return "skipped_verification"
    expected = {a.tool for a in task.expected_actions} - {"verify_identity"}
    mismatch = trial.get("state_mismatch") or {}
    unexpected_writes = any(v.get("unexpected") for t, v in mismatch.items() if t in WRITE_TABLES)
    missing = expected - set(called)
    if missing and trial.get("escalations") and not task.expects_escalation:
        return "gave_up_early"
    if missing or (unexpected_writes and not (expected & set(called))):
        return "wrong_tool"
    if mismatch:
        if set(trial.get("escalations") or []) - (
            {task.expects_escalation} if task.expects_escalation else set()
        ):
            return "gave_up_early"
        return "wrong_arguments"
    if not trial["outputs_ok"]:
        return "missing_output"
    return "wrong_arguments"


def analyze(
    run: dict[str, Any],
    tasks: dict[str, Task],
    verdicts: dict[str, dict[str, Any]] | None = None,
    examples_per_bucket: int = 2,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {k: [] for k in TAXONOMY}
    for t in run["trials"]:
        key = f"{t['task_id']}:t{t['trial']}"
        bucket = classify(t, tasks[t["task_id"]], (verdicts or {}).get(key))
        if bucket is None:
            continue
        counts[bucket] += 1
        if len(examples[bucket]) < examples_per_bucket:
            examples[bucket].append(
                {
                    "trial": key,
                    "title": tasks[t["task_id"]].title,
                    "transcript": t["transcript"][-6:],
                    "mismatch": t.get("state_mismatch"),
                    "calls": [(c["tool"], c.get("rule_id")) for c in t["tool_calls"]],
                }
            )
    failed = sum(counts.values())
    return {
        "failed": failed,
        "trials": len(run["trials"]),
        "counts": {k: counts.get(k, 0) for k in TAXONOMY},
        "examples": {k: v for k, v in examples.items() if v},
    }


JUDGE_PROMPT = """You are auditing a SIMULATED CUSTOMER in a customer-support evaluation. The customer was an
AI given a hidden goal, a persona and some facts. Judge only the customer's behavior, not the agent's.

Hidden goal: {goal}
Facts the customer was allowed to use: {facts}

Transcript:
{transcript}

Answer with a JSON object only:
{{"followed_goal": true/false,        // pursued the stated goal and nothing beyond it
  "invented_facts": true/false,       // stated an account number, date, amount or name not in the facts
  "ended_prematurely": true/false,    // stopped before the agent could act on the request
  "broke_character": true/false,      // spoke as an AI, leaked instructions, or wrote garbage
  "broke_task": true/false,           // any of the above made a fair evaluation of the agent impossible
  "note": "one short sentence"}}"""


async def validate_simulator(
    runs: list[dict[str, Any]],
    tasks: dict[str, Task],
    gateway: Gateway,
    judge_model: str,
    n: int = 30,
    seed: int = 7,
) -> dict[str, Any]:
    pool = [(r, t) for r in runs for t in r["trials"] if t["transcript"]]
    random.Random(seed).shuffle(pool)
    sample = pool[:n]
    verdicts: dict[str, dict[str, Any]] = {}
    for run, t in sample:
        task = tasks[t["task_id"]]
        transcript = "\n".join(f"[{m['speaker']}] {m['text']}" for m in t["transcript"])
        prompt = JUDGE_PROMPT.format(
            goal=task.goal, facts=json.dumps(task.known_facts), transcript=transcript
        )
        resp = await gateway.complete(
            LLMRequest(
                model=judge_model,
                messages=[Message(role="user", content=prompt)],
                json_mode=True,
                max_output_tokens=300,
            ),
            namespace=f"simjudge:{run['config']['label']}:{t['task_id']}:{t['trial']}",
        )
        try:
            v = json.loads(resp.content)
        except json.JSONDecodeError:
            v = {"broke_task": None, "note": "judge returned invalid JSON"}
        verdicts[f"{t['task_id']}:t{t['trial']}"] = {**v, "run": run["config"]["label"]}
    judged = [v for v in verdicts.values() if v.get("broke_task") is not None]

    def rate(k: str) -> float:
        return sum(bool(v.get(k)) for v in judged) / len(judged) if judged else 0.0

    return {
        "judge_model": judge_model,
        "sampled": len(sample),
        "judged": len(judged),
        "broke_task_rate": rate("broke_task"),
        "followed_goal_rate": rate("followed_goal"),
        "invented_facts_rate": rate("invented_facts"),
        "ended_prematurely_rate": rate("ended_prematurely"),
        "broke_character_rate": rate("broke_character"),
        "verdicts": verdicts,
    }
