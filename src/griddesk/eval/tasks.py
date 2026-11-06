"""Eval task schema and loader.

A task is what tau-bench calls a scenario: a hidden goal and persona for the simulated customer,
optional setup applied on top of the seed, and the ground truth. Ground truth is a list of
`expected_actions` (tool calls with arguments, in order). The harness replays them on a fresh
trial database to get the expected end state, so the "rows that must change, and the ones that
must not" are derived, not hand-copied. `required_outputs` are facts the agent must say (for
read-only tasks) and `forbidden_outputs` are things it must never say (other people's data).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Domain = Literal["billing", "outages", "accounts", "mixed"]
Difficulty = Literal["easy", "medium", "hard"]
Kind = Literal["resolve", "refuse", "escalate", "attack"]


class ExpectedAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class SetupOp(BaseModel):
    """A row change applied before the conversation: `update` an existing row or `insert` a new one."""

    op: Literal["update", "insert"]
    table: str
    key: dict[str, Any] = Field(default_factory=dict)  # for update: primary-key columns
    values: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    domain: Domain
    difficulty: Difficulty
    kind: Kind = "resolve"
    title: str
    persona: str
    goal: str  # hidden instructions for the simulated customer
    known_facts: dict[str, str] = Field(default_factory=dict)  # what the customer knows and can share
    first_message: str | None = None  # optional fixed opening line
    setup: list[SetupOp] = Field(default_factory=list)
    expected_actions: list[ExpectedAction] = Field(default_factory=list)
    operator_decision: Literal["approve", "deny"] = "approve"
    expects_approval: bool = False
    expects_escalation: str | None = None  # escalation category, if the right outcome includes one
    # Escalation categories a compliant agent may or may not raise, ignored by the end-state comparison
    # and by escalation accuracy. Used for P-31: when the customer insists on something the handbook does
    # not allow, a `complaint` escalation is correct but so is a plain refusal the customer accepts.
    acceptable_escalations: list[str] = Field(default_factory=list)
    # True when asking a human for approval is as correct as refusing outright (approval accuracy then
    # counts either outcome as right). The operator decision still decides the end state.
    approval_optional: bool = False
    required_outputs: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)
    attack: str | None = None  # attack family, for the adversarial suite
    max_turns: int = 10
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _escalations_consistent(self) -> Task:
        if self.expects_escalation and self.expects_escalation in self.acceptable_escalations:
            raise ValueError(f"{self.id}: {self.expects_escalation} is both expected and optional")
        optional = [
            a.args.get("category")
            for a in self.expected_actions
            if a.tool == "escalate_to_human" and a.args.get("category") in self.acceptable_escalations
        ]
        if optional:
            raise ValueError(f"{self.id}: ground truth escalates with optional category {optional[0]}")
        return self


def load_tasks(path: Path) -> list[Task]:
    """Load every task from a YAML file or a directory of YAML files, sorted by id."""
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    tasks: list[Task] = []
    for f in files:
        data = yaml.safe_load(f.read_text()) or []
        tasks.extend(Task.model_validate(t) for t in data)
    ids = [t.id for t in tasks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate task ids: {sorted(dupes)}")
    return sorted(tasks, key=lambda t: t.id)
