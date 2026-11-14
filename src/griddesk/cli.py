"""`griddesk` command line. Subcommands are added as features land."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import typer

from griddesk import __version__

app = typer.Typer(
    help="GridDesk: utility customer-ops agent and its evaluation harness.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # locals can hold secrets
)


@app.callback()
def main() -> None:
    """GridDesk command line."""
    import logging

    for noisy in ("httpx", "mcp", "mcp.server", "mcp.client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("seed")
def db_seed(
    seed: int = typer.Option(20261015, help="Random seed for the generator."),
    force: bool = typer.Option(False, "--force", help="Allow reseeding a non-local database host."),
    if_empty: bool = typer.Option(False, "--if-empty", help="Only seed when there are no customers yet."),
) -> None:
    """Replace the local database contents with freshly generated synthetic data."""
    from sqlalchemy import text

    from griddesk.db.seed import generate, load
    from griddesk.db.session import get_engine

    if if_empty:
        with get_engine().connect() as c:
            if c.execute(text("select count(*) from customers")).scalar():
                typer.echo("database already seeded; skipping")
                return

    counts = load(get_engine(), generate(seed), force=force)
    for table, n in counts.items():
        if n:
            typer.echo(f"{table:22s} {n:>7,}")


eval_app = typer.Typer(help="Run and compare evaluations.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


def setup_logging() -> str:
    """Configure structured logging and install the PII redactor from settings; return its name.

    Every command that runs agents or tools calls this first, so logs, run files and audit details
    are redacted by Presidio (or the regex fallback when `PII_REDACTOR=regex`).
    """
    from griddesk.config import get_settings
    from griddesk.guardrails.pii import install_from_settings
    from griddesk.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    return install_from_settings(settings)


_last_gateway: Any = None


def execute_suite(
    *,
    model: str,
    suite: str,
    trials: int,
    design: str,
    enforce: bool,
    sim_model: str,
    task_ids: list[str],
    limit: int,
    concurrency: int,
    cache_dir: Path,
    cache_mode: str,
    label: str,
    defenses: Any = None,
    sim_fallbacks: list[str] | None = None,
) -> dict[str, Any]:
    """Load tasks, run them, return the run dict (shared by `eval run` and `eval gate`)."""
    import asyncio

    setup_logging()

    from griddesk.agents.runtime import Design
    from griddesk.config import REPO_ROOT
    from griddesk.db.session import get_engine
    from griddesk.eval.runner import EvalConfig, TrialRecord, run_suite
    from griddesk.eval.tasks import load_tasks
    from griddesk.gateway import build_gateway
    from griddesk.gateway.types import CacheMode

    suite_path = Path(suite) if suite.endswith(".yaml") else REPO_ROOT / "evals" / suite
    selected = load_tasks(suite_path)
    if task_ids:
        wanted = set(task_ids)
        unknown = wanted - {t.id for t in selected}
        if unknown:
            raise typer.BadParameter(f"unknown task ids: {sorted(unknown)}")
        selected = [t for t in selected if t.id in wanted]
    if limit:
        selected = selected[:limit]
    cfg = EvalConfig(
        model=model,
        sim_model=sim_model,
        design=cast(Design, design),
        enforce_policy=enforce,
        trials=trials,
        concurrency=concurrency,
        suite=suite,
        label=label,
    )
    if defenses is not None:
        cfg.defenses = defenses
    if sim_fallbacks is not None:
        cfg.sim_fallbacks = sim_fallbacks
    gw = build_gateway(cache_dir=cache_dir, cache_mode=cast(CacheMode, cache_mode))
    global _last_gateway
    _last_gateway = gw

    def progress(r: TrialRecord) -> None:
        mark = "PASS" if r.success else ("ERR " if r.error else "fail")
        err = f" error={r.error[:80]}" if r.error else ""
        typer.echo(f"  {mark} {r.task_id} t{r.trial} turns={r.turns} calls={len(r.tool_calls)}{err}")

    typer.echo(f"Running {len(selected)} tasks x {trials} trials on {model} ({design}, enforce={enforce})")

    async def go() -> dict[str, Any]:
        try:
            return await run_suite(selected, cfg, gw, get_engine(), progress)
        finally:
            await gw.aclose()

    return asyncio.run(go())


def parse_defenses(spec: str) -> Any:
    from griddesk.guardrails import Defenses

    if spec == "all":
        return Defenses()
    if spec == "none":
        return Defenses.none()
    on = set(spec.split("+"))
    unknown = on - set(Defenses.__dataclass_fields__)
    if unknown:
        raise typer.BadParameter(f"unknown defenses: {sorted(unknown)}")
    return Defenses(**{f: f in on for f in Defenses.__dataclass_fields__})


def print_summary(run: dict[str, Any]) -> None:
    s = run["summary"]
    typer.echo(
        f"\nsuccess {s['success_rate']:.1%}  CI95 [{s['success_ci95'][0]:.1%}, {s['success_ci95'][1]:.1%}]  "
        f"violations {s['violation_rate']:.1%}  errors {s['errors']}  "
        f"cost/resolved ${s['cost_per_resolved_usd']:.4f}"
    )
    for k in range(1, run["config"]["trials"] + 1):
        typer.echo(f"pass^{k} {s[f'pass^{k}']:.1%}")


@eval_app.command("run")
def eval_run(
    model: str = typer.Option(..., help="Agent model, e.g. gemini:gemini-3.8-flash"),
    suite: str = typer.Option("tasks", help="Folder under evals/ (tasks, adversarial) or a YAML path."),
    trials: int = typer.Option(1, min=1, help="Trials per task (k for pass^k)."),
    design: str = typer.Option("single", help="Agent design: single or multi."),
    enforce: bool = typer.Option(True, "--enforce/--no-enforce", help="In-tool policy enforcement."),
    sim_model: str = typer.Option("groq:openai/gpt-oss-120b", help="User simulator model."),
    tasks: str = typer.Option("", help="Comma-separated task ids to run (default: all)."),
    task_file: Path | None = typer.Option(
        None, help="File of task ids, one per line (e.g. evals/subsets/core-30.txt)."
    ),
    defenses: str = typer.Option(
        "all", help="all, none, or a + list of in_tool_policy, action_guard, spotlight, injection_filter."
    ),
    sim_fallbacks: str = typer.Option("groq:openai/gpt-oss-20b", help="Comma-separated simulator fallbacks."),
    limit: int = typer.Option(0, help="Run only the first N tasks."),
    concurrency: int = typer.Option(2, min=1),
    replay: bool = typer.Option(False, help="Serve only from the response cache; fail on a miss."),
    cache_dir: Path = typer.Option(Path(".griddesk/cache"), help="Response cache directory."),
    out: Path | None = typer.Option(None, help="Run file path (default evals/runs/<label>.json)."),
    label: str = typer.Option("", help="Run label (default derived from the config)."),
) -> None:
    """Run a task suite and write a run file."""
    from griddesk.config import REPO_ROOT
    from griddesk.eval.runner import write_run

    slug = model.split(":", 1)[1].replace("/", "-")
    name = label or f"{Path(suite).stem}-{slug}-{design}-{'enf' if enforce else 'noenf'}-k{trials}"
    ids = [t for t in tasks.split(",") if t]
    if task_file:
        ids += [
            ln.strip() for ln in task_file.read_text().splitlines() if ln.strip() and not ln.startswith("#")
        ]
        if not ids:
            raise typer.BadParameter(f"{task_file} lists no task ids")
    run = execute_suite(
        model=model,
        suite=suite,
        trials=trials,
        design=design,
        enforce=enforce,
        sim_model=sim_model,
        task_ids=ids,
        limit=limit,
        concurrency=concurrency,
        cache_dir=cache_dir,
        cache_mode="replay_only" if replay else "read_write",
        label=name,
        defenses=parse_defenses(defenses),
        sim_fallbacks=[x for x in sim_fallbacks.split(",") if x],
    )
    path = write_run(run, out or REPO_ROOT / "evals" / "runs" / f"{name}.json")
    print_summary(run)
    typer.echo(f"wrote {path}")


@eval_app.command("gate")
def eval_gate(
    config: Path = typer.Option(Path("evals/ci/gate.yaml")),
    record: bool = typer.Option(False, help="Record live into evals/cache and rewrite the baseline."),
    markdown: Path | None = typer.Option(None, help="Write the PR comment body here."),
    out: Path = typer.Option(Path(".griddesk/ci-gate-run.json"), help="Where to write the replayed run."),
    prune: bool = typer.Option(False, help="After a clean replay, delete unused cache entries."),
) -> None:
    """CI regression gate: replay the slice from the committed cache and compare with the baseline."""
    from griddesk.config import REPO_ROOT
    from griddesk.eval.gate import evaluate, load_gate
    from griddesk.eval.report import load_run
    from griddesk.eval.runner import write_run

    gate = load_gate(config)
    run = execute_suite(
        model=gate.model,
        suite=gate.suite,
        trials=gate.trials,
        design="single",
        enforce=True,
        sim_model="groq:openai/gpt-oss-120b",
        task_ids=gate.tasks,
        limit=0,
        concurrency=3,
        cache_dir=REPO_ROOT / "evals" / "cache",
        cache_mode="read_write" if record else "replay_only",
        label="ci-gate",
    )
    print_summary(run)
    baseline_path = REPO_ROOT / gate.baseline
    if record:
        write_run(run, baseline_path)
        typer.echo(f"recorded baseline {baseline_path}")
    if record or prune:
        # Keep only the entries this slice used, so stale recordings do not pile up in git.
        cache = _last_gateway.cache if _last_gateway else None
        if cache is not None and not any(t.get("error") for t in run["trials"]):
            typer.echo(f"pruned {cache.prune(cache.accessed)} unused cache entries")
    if record:
        return
    write_run(run, out)
    result = evaluate(run, load_run(baseline_path), gate.thresholds)
    if markdown:
        markdown.write_text(result.markdown)
    typer.echo(result.markdown)
    if not result.passed:
        raise typer.Exit(code=1)


@eval_app.command("build-tasks")
def eval_build_tasks(
    out: Path | None = typer.Option(None, help="Rebuild only this suite file (default: every suite)."),
) -> None:
    """Regenerate template-built task suites from the deterministic seed (run `make seed` first)."""
    from griddesk.config import REPO_ROOT
    from griddesk.db.session import live_session_factory
    from griddesk.eval.taskgen import build, suites, write

    all_suites = suites()
    if out is not None:
        key = next(
            (k for k in all_suites if (REPO_ROOT / k).resolve() == out.resolve() or Path(k) == out), None
        )
        if key is None:
            typer.echo(f"unknown suite {out}; known: {', '.join(all_suites)}", err=True)
            raise typer.Exit(code=1)
        all_suites = {key: all_suites[key]}
    with live_session_factory()() as s:
        for rel, templates in all_suites.items():
            tasks = build(s, templates)
            path = out if out is not None else REPO_ROOT / rel
            write(tasks, path)
            typer.echo(f"wrote {len(tasks)} tasks to {path}")


def _all_tasks() -> dict[str, Any]:
    from griddesk.config import REPO_ROOT
    from griddesk.eval.tasks import load_tasks

    out = {t.id: t for t in load_tasks(REPO_ROOT / "evals" / "tasks")}
    out.update({t.id: t for t in load_tasks(REPO_ROOT / "evals" / "adversarial")})
    return out


@eval_app.command("build-subset")
def eval_build_subset(
    n: int = typer.Option(30, help="How many tasks."),
    out: Path = typer.Option(Path("evals/subsets/core-30.txt")),
) -> None:
    """Write a deterministic stratified subset of evals/tasks (domain x difficulty)."""
    from griddesk.config import REPO_ROOT
    from griddesk.eval.subsets import render, stratified
    from griddesk.eval.tasks import load_tasks

    ids = stratified(load_tasks(REPO_ROOT / "evals" / "tasks"), n)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ids))
    typer.echo(f"wrote {len(ids)} task ids to {out}")


@eval_app.command("analyze")
def eval_analyze(
    run_file: Path,
    verdicts: Path | None = typer.Option(None, help="Simulator-validation JSON (marks simulator errors)."),
    out: Path | None = typer.Option(None, help="Write the analysis JSON here."),
) -> None:
    """Sort failed trials into the error taxonomy and print counts with examples."""
    import json

    from griddesk.eval.analysis import analyze
    from griddesk.eval.report import load_run

    v = json.loads(verdicts.read_text())["verdicts"] if verdicts else None
    result = analyze(load_run(run_file), _all_tasks(), v)
    typer.echo(f"{result['failed']} failed of {result['trials']} trials")
    for k, n in result["counts"].items():
        typer.echo(f"  {k:22s} {n}")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, default=str))
        typer.echo(f"wrote {out}")


@eval_app.command("validate-sim")
def eval_validate_sim(
    run_files: list[Path],
    judge: str = typer.Option(
        "groq:qwen/qwen3.8-27b", help="Judge model; must differ from the simulator's family (gpt-oss)."
    ),
    n: int = typer.Option(30),
    out: Path = typer.Option(Path("evals/analysis/simulator-validation.json")),
) -> None:
    """Have a judge model check sampled transcripts against their hidden goals."""
    import asyncio
    import json

    from griddesk.eval.analysis import validate_simulator
    from griddesk.eval.report import load_run
    from griddesk.gateway import build_gateway

    gw = build_gateway()

    async def go() -> dict[str, Any]:
        try:
            return await validate_simulator([load_run(p) for p in run_files], _all_tasks(), gw, judge, n=n)
        finally:
            await gw.aclose()

    result = asyncio.run(go())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    typer.echo(
        f"judged {result['judged']}/{result['sampled']}: simulator broke the task in "
        f"{result['broke_task_rate']:.1%}; followed goal {result['followed_goal_rate']:.1%}"
    )
    typer.echo(f"wrote {out}")


@eval_app.command("results")
def eval_results(
    readme: Path | None = typer.Option(None, help="Defaults to the repository README."),
    check: bool = typer.Option(False, help="Fail if the README does not match the run files."),
) -> None:
    """Fill the README results blocks from the committed run and analysis files."""
    from griddesk.config import REPO_ROOT
    from griddesk.eval.readme import render

    readme = readme or REPO_ROOT / "README.md"
    text = readme.read_text()
    new = render(text, readme.resolve().parent)
    if check:
        if new != text:
            typer.echo("README results are stale; run `griddesk eval results`")
            raise typer.Exit(1)
        typer.echo("README results match the run files")
        return
    readme.write_text(new)
    typer.echo(f"wrote results into {readme}")


@eval_app.command("export-dashboard")
def eval_export_dashboard(out: Path = typer.Option(Path("web/dist-static/eval-data.json"))) -> None:
    """Write the static dashboard's eval-data.json from the committed run files."""
    import json

    from griddesk.api.app import RUNS_DIR, run_summary

    runs, details = [], {}
    for p in sorted(RUNS_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        runs.append(run_summary(p, data))
        details[p.stem] = data
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"runs": runs, "details": details}, default=str))
    typer.echo(f"wrote {len(runs)} runs to {out}")


@eval_app.command("compare")
def eval_compare(run_a: Path, run_b: Path) -> None:
    """Print metric deltas between two run files with a paired significance test."""
    from griddesk.eval.report import compare, load_run

    text, _, _ = compare(load_run(run_a), load_run(run_b))
    typer.echo(text)


if __name__ == "__main__":
    app()
