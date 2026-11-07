import json

from griddesk.eval.results import attack_per_task, attack_table, attacked, model_table, paired_table


def trial(task, i, success, state=True, violation=False, error=None):
    return {
        "task_id": task,
        "trial": i,
        "success": success,
        "state_match": state,
        "violation": violation,
        "error": error,
    }


def run_file(tmp_path, name, trials, model="ollama:qwen3:8b"):
    summary = {
        "success_rate": sum(t["success"] for t in trials) / len(trials),
        "violation_rate": 0.0,
        "tool_call_validity": 1.0,
        "latency_p50_ms": 1000,
        "latency_p95_ms": 2000,
        "tokens_per_resolved": 100,
        "cost_per_resolved_usd": 0.0,
        "attempted_violations_per_trial": 0.5,
    }
    p = tmp_path / f"{name}.json"
    p.write_text(
        json.dumps({"config": {"model": model, "label": name}, "summary": summary, "trials": trials})
    )
    return p


def test_attack_definition_matches_the_runner():
    assert attacked(trial("a", 0, False, state=False))
    assert attacked(trial("a", 0, True, violation=True))
    # Missing a required phrase fails the task but is not a successful attack.
    assert not attacked(trial("a", 0, False, state=True))


def test_errored_trials_are_left_out_of_attack_rates():
    r = {"trials": [trial("a", 0, False, state=False), trial("a", 1, False, error="ProviderError")]}
    assert attack_per_task(r) == {"a": [1.0]}


def test_tables_render_from_run_files(tmp_path):
    a = run_file(tmp_path, "a", [trial("x", 0, True), trial("y", 0, False)])
    b = run_file(tmp_path, "b", [trial("x", 0, True), trial("y", 0, True)])
    assert "`ollama:qwen3:8b`" in model_table([a])
    assert "| 2 |" in paired_table([("design", a, b, "success")])
    att = attack_table(
        [("all", run_file(tmp_path, "c", [trial("x", 0, False, state=False), trial("y", 0, True)]))]
    )
    assert "50.0%" in att
