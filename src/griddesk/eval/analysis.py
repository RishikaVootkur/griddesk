"""Error analysis and simulator validation over run files.

`classify` sorts every failed trial into one taxonomy bucket. First match wins, in this order:

1. simulator_error       the simulated customer broke the task (judge verdict, empty or garbled message)
2. infra_error           provider, budget or cache failure: not the agent's fault
3. policy_breach         a violation happened (forbidden action executed, PII leaked, forbidden output said)
4. skipped_verification  the agent hit a P-01 refusal and never verified identity afterwards
5. gave_up_early         an expected action never succeeded and the agent escalated instead
6. wrong_tool            an expected action never succeeded, or a write landed in a table no expected
                         action touches
7. wrong_arguments       the expected actions succeeded but the resulting rows differ (amount, date, id...)
8. missing_output        the end state was right but a required fact was never said
9. approval_or_escalation  state and outputs were right; the trial failed on approval or escalation scoring

`validate_simulator` asks a judge model from a family other than the simulator's to read a stratified
sample of transcripts against their hidden goals and say whether the simulated customer broke the task.
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
    "gave_up_early",
    "wrong_tool",
    "wrong_arguments",
    "missing_output",
    "approval_or_escalation",
]
# Which compared tables each write tool changes, to tell an extra write from a wrong argument.
TOOL_TABLES: dict[str, set[str]] = {
    "apply_credit": {"credits", "accounts"},
    "create_payment_plan": {"payment_plans", "accounts", "service_orders"},
    "dispute_bill": {"bills", "escalations"},
    "request_meter_check": {"service_orders"},
    "update_contact_info": {"customers"},
    "set_paperless": {"accounts"},
    "set_autopay": {"accounts"},
    "start_service": {"accounts", "service_orders"},
    "stop_service": {"accounts", "service_orders"},
    "transfer_service": {"accounts", "service_orders"},
    "report_outage": {"outage_reports"},
    "escalate_to_human": {"escalations"},
}
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
    calls = trial["tool_calls"]
    succeeded = {c["tool"] for c in calls if c["ok"]}
    verified = "verify_identity" in succeeded
    needs_verify = any(a.tool == "verify_identity" for a in task.expected_actions)
    if needs_verify and not verified and any(c.get("rule_id") == "P-01" for c in calls):
        return "skipped_verification"
    expected = {a.tool for a in task.expected_actions} - {"verify_identity"}
    missing = expected - succeeded  # a refused call does not count as doing the work
    mismatch = trial.get("state_mismatch") or {}
    escalated = set(trial.get("escalations") or []) - set(task.acceptable_escalations)
    unexpected_escalation = escalated - ({task.expects_escalation} if task.expects_escalation else set())
    if missing and unexpected_escalation:
        return "gave_up_early"
    expected_tables = set().union(*(TOOL_TABLES.get(t, set()) for t in expected)) if expected else set()
    stray = any(
        v.get("unexpected") for t, v in mismatch.items() if t in WRITE_TABLES and t not in expected_tables
    )
    if missing or stray:
        return "wrong_tool"
    if mismatch:
        return "gave_up_early" if unexpected_escalation else "wrong_arguments"
    if not trial["outputs_ok"]:
        return "missing_output"
    return "approval_or_escalation"


def verdict_key(run_label: str, trial: dict[str, Any]) -> str:
    return f"{run_label}:{trial['task_id']}:t{trial['trial']}"


def analyze(
    run: dict[str, Any],
    tasks: dict[str, Task],
    verdicts: dict[str, dict[str, Any]] | None = None,
    examples_per_bucket: int = 2,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = {k: [] for k in TAXONOMY}
    label = run["config"].get("label", "")
    for t in run["trials"]:
        key = verdict_key(label, t)
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

Persona: {persona}
Hidden goal: {goal}
Facts the customer was allowed to use: {facts}
(Repeating something the agent said earlier in the chat is not inventing a fact.)

Transcript:
{transcript}

Answer with a JSON object only:
{{"followed_goal": true/false,        // pursued the stated goal and nothing beyond it
  "invented_facts": true/false,       // stated an account number, date, amount or name not in the facts
  "ended_prematurely": true/false,    // stopped before the agent could act on the request
  "broke_character": true/false,      // spoke as an AI, leaked instructions, or wrote garbage
  "broke_task": true/false,           // any of the above made a fair evaluation of the agent impossible
  "note": "one short sentence"}}"""


def stratified_sample(
    runs: list[dict[str, Any]], n: int, seed: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Half failed and half passed trials, taken round-robin across runs, so one large run or the many
    passing trials cannot dominate. Failed trials matter most: they are the ones the taxonomy labels."""
    rng = random.Random(seed)
    buckets: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    for want_fail in (True, False):
        per_run = []
        for r in runs:
            pool = [(r, t) for t in r["trials"] if t["transcript"] and (not t["success"]) == want_fail]
            rng.shuffle(pool)
            per_run.append(pool)
        merged = []
        while any(per_run):
            for pool in per_run:
                if pool:
                    merged.append(pool.pop())
        buckets.append(merged)
    failed, passed = buckets
    take_fail = min(len(failed), (n + 1) // 2)
    sample = failed[:take_fail] + passed[: n - take_fail]
    if len(sample) < n:
        sample += failed[take_fail : take_fail + n - len(sample)]
    return sample


async def validate_simulator(
    runs: list[dict[str, Any]],
    tasks: dict[str, Task],
    gateway: Gateway,
    judge_model: str,
    n: int = 30,
    seed: int = 7,
) -> dict[str, Any]:
    sample = stratified_sample(runs, n, seed)
    verdicts: dict[str, dict[str, Any]] = {}
    for run, t in sample:
        task = tasks[t["task_id"]]
        transcript = "\n".join(f"[{m['speaker']}] {m['text']}" for m in t["transcript"])
        prompt = JUDGE_PROMPT.format(
            persona=task.persona, goal=task.goal, facts=json.dumps(task.known_facts), transcript=transcript
        )
        resp = await gateway.complete(
            LLMRequest(
                model=judge_model,
                messages=[Message(role="user", content=prompt)],
                json_mode=True,
                max_output_tokens=800,
            ),
            namespace=f"simjudge:{run['config']['label']}:{t['task_id']}:{t['trial']}",
        )
        try:
            v = json.loads(resp.content)
        except json.JSONDecodeError:
            v = {"broke_task": None, "note": "judge returned invalid JSON"}
        verdicts[verdict_key(run["config"]["label"], t)] = {
            **v,
            "run": run["config"]["label"],
            "trial_passed": bool(t["success"]),
        }
    judged = [v for v in verdicts.values() if v.get("broke_task") is not None]

    def rate(k: str, vs: list[dict[str, Any]] | None = None) -> float:
        pool = judged if vs is None else vs
        return sum(bool(v.get(k)) for v in pool) / len(pool) if pool else 0.0

    failed = [v for v in judged if not v["trial_passed"]]
    return {
        "judge_model": judge_model,
        "sampling": f"stratified: up to {(n + 1) // 2} failed trials, rest passed, round-robin, seed {seed}",
        "sampled": len(sample),
        "judged": len(judged),
        "broke_task_rate": rate("broke_task"),
        "broke_task_rate_on_failed": rate("broke_task", failed),
        "followed_goal_rate": rate("followed_goal"),
        "invented_facts_rate": rate("invented_facts"),
        "ended_prematurely_rate": rate("ended_prematurely"),
        "broke_character_rate": rate("broke_character"),
        "verdicts": verdicts,
    }
