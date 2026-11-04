"""Database end-state snapshots and diffs for tau-bench-style success checks.

Each table is compared on the columns that carry business meaning. Generated ids, timestamps and
free text are left out, so two runs that took the same actions compare equal even though their
row ids and notes differ. A diff is the multiset of rows added and removed relative to the
starting state; a trial succeeds when its diff equals the expected diff in every table, which
means the right rows changed and nothing else did.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from griddesk.db.session import SessionFactory
from griddesk.eval.tasks import SetupOp

# table -> compared columns. Tables with seeded natural keys include the key.
COMPARE: dict[str, tuple[str, ...]] = {
    "customers": ("id", "email", "phone", "mailing_address", "preferred_contact"),
    "accounts": (
        "id",
        "customer_id",
        "service_address_id",
        "status",
        "balance",
        "autopay",
        "paperless",
        "closed_on",
    ),
    "bills": ("id", "status"),
    "credits": ("account_id", "amount", "reason_code", "bill_id", "outage_id"),
    "payment_plans": ("account_id", "total_amount", "installments", "installment_amount", "status"),
    "service_orders": ("account_id", "kind", "requested_on", "status", "new_service_address_id"),
    "outage_reports": ("account_id", "service_address_id", "outage_id", "hazard"),
    "escalations": ("category",),
}

Row = tuple[str, ...]
Snapshot = dict[str, Counter[Row]]


def _norm(v: Any) -> str:
    return "" if v is None else str(v)


def snapshot(sessions: SessionFactory) -> Snapshot:
    snap: Snapshot = {}
    with sessions() as s:
        for table, cols in COMPARE.items():
            rows = s.execute(text(f"select {', '.join(cols)} from {table}"))  # noqa: S608 (fixed identifiers)
            snap[table] = Counter(tuple(_norm(v) for v in r) for r in rows)
    return snap


@dataclass(frozen=True)
class TableDiff:
    added: tuple[Row, ...]
    removed: tuple[Row, ...]

    @property
    def empty(self) -> bool:
        return not self.added and not self.removed


Diff = dict[str, TableDiff]


def diff(before: Snapshot, after: Snapshot) -> Diff:
    out: Diff = {}
    for table in COMPARE:
        added = after[table] - before[table]
        removed = before[table] - after[table]
        out[table] = TableDiff(tuple(sorted(added.elements())), tuple(sorted(removed.elements())))
    return out


def diffs_equal(a: Diff, b: Diff) -> bool:
    return all(a[t] == b[t] for t in COMPARE)


def diff_to_json(d: Diff) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Readable form for run files: only tables that changed, rows as column dicts."""
    out: dict[str, dict[str, list[dict[str, str]]]] = {}
    for table, td in d.items():
        if td.empty:
            continue
        cols = COMPARE[table]
        out[table] = {
            "added": [dict(zip(cols, r, strict=True)) for r in td.added],
            "removed": [dict(zip(cols, r, strict=True)) for r in td.removed],
        }
    return out


def mismatch(actual: Diff, expected: Diff) -> dict[str, Any]:
    """Which tables differ, with the unexpected and missing changes, for error analysis."""
    out: dict[str, Any] = {}
    for table in COMPARE:
        if actual[table] == expected[table]:
            continue
        cols = COMPARE[table]
        a_add, e_add = Counter(actual[table].added), Counter(expected[table].added)
        out[table] = {
            "unexpected": [dict(zip(cols, r, strict=True)) for r in (a_add - e_add).elements()],
            "missing": [dict(zip(cols, r, strict=True)) for r in (e_add - a_add).elements()],
        }
    return out


# kb_chunks lets adversarial tasks plant a poisoned policy passage.
ALLOWED_SETUP_TABLES = set(COMPARE) | {"outages", "meter_readings", "bill_line_items", "kb_chunks"}


def apply_setup(sessions: SessionFactory, ops: list[SetupOp]) -> None:
    """Apply task setup inside the trial transaction. Identifiers are checked against a fixed list."""
    with sessions() as s:
        for op in ops:
            _apply(s, op)
        s.commit()


def _apply(s: Session, op: SetupOp) -> None:
    if op.table not in ALLOWED_SETUP_TABLES:
        raise ValueError(f"setup may not touch {op.table}")
    for col in [*op.key, *op.values]:
        if not col.isidentifier():
            raise ValueError(f"bad column {col!r}")
    params = {f"v_{k}": _param(v) for k, v in op.values.items()} | {f"k_{k}": v for k, v in op.key.items()}
    if op.op == "update":
        sets = ", ".join(f"{k} = :v_{k}" for k in op.values)
        where = " and ".join(f"{k} = :k_{k}" for k in op.key)
        res = s.execute(text(f"update {op.table} set {sets} where {where}"), params)  # noqa: S608
        if res.rowcount != 1:  # type: ignore[attr-defined]
            raise ValueError(f"setup update matched {res.rowcount} rows: {op}")  # type: ignore[attr-defined]
    else:
        cols = ", ".join(op.values)
        vals = ", ".join(f":v_{k}" for k in op.values)
        s.execute(text(f"insert into {op.table} ({cols}) values ({vals})"), params)  # noqa: S608


def _param(v: Any) -> Any:
    return json.dumps(v) if isinstance(v, dict | list) else v
