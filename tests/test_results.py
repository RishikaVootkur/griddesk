import json
from pathlib import Path

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


def test_unauthorized_change_needs_an_unexpected_row():
    from griddesk.eval.results import attack_counts, unauthorized_change

    wrote = {"state_mismatch": {"credits": {"unexpected": [{"id": 1}], "missing": []}}}
    missed = {"state_mismatch": {"credits": {"unexpected": [], "missing": [{"id": 1}]}}}
    assert unauthorized_change(wrote) and not unauthorized_change(missed)
    run = {
        "trials": [
            {**wrote, "violation": True},
            {**missed, "violation": True},
            {"state_mismatch": {}, "violation": False},
            {"state_mismatch": {}, "violation": True, "error": "ProviderError"},
        ]
    }
    assert attack_counts(run) == (3, 1, 1)


def test_readme_blocks_are_filled_and_idempotent(monkeypatch):
    from griddesk.eval import readme

    monkeypatch.setattr(readme, "BLOCKS", {"HEADLINE": lambda root: "| a |", "TABLES": lambda root: "t"})
    text = "x\n<!-- RESULTS:HEADLINE -->\n\ny\n<!-- RESULTS:TABLES -->\nz"
    once = readme.render(text, Path("."))
    assert "<!-- RESULTS:HEADLINE -->\n| a |\n<!-- /RESULTS:HEADLINE -->" in once
    assert readme.render(once, Path(".")) == once
    assert once.endswith("<!-- /RESULTS:TABLES -->\nz")


def test_readme_without_markers_is_refused():
    import pytest

    from griddesk.eval import readme

    with pytest.raises(ValueError):
        readme.render("no markers", Path("."))


def test_committed_readme_matches_the_run_files():
    from griddesk.config import REPO_ROOT
    from griddesk.eval import readme

    text = (REPO_ROOT / "README.md").read_text()
    assert readme.render(text, REPO_ROOT) == text
