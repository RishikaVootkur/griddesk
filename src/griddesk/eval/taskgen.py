"""Build eval tasks from the deterministic seed.

Each scenario template picks seeded customers that fit it (SQL), writes the hidden goal and persona
for the simulator, and states the ground-truth actions. The seed is deterministic, so the generated
YAML is stable and is committed; rerun `griddesk eval build-tasks` after changing the seed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from griddesk.config import get_settings

TODAY = get_settings().simulated_now.date()
STYLES = [
    "polite and patient, writes short messages",
    "busy and a little impatient, wants this sorted quickly",
    "friendly and chatty, but stays on topic",
    "anxious about money, asks for reassurance",
    "direct and businesslike",
    "elderly, careful, asks the agent to repeat important numbers",
]


@dataclass
class Fixture:
    account_id: str
    customer_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    ssn_last4: str
    extra: dict[str, Any]


def fixtures(
    s: Session,
    where: str,
    n: int,
    extra_cols: str = "",
    params: dict[str, Any] | None = None,
    order: str = "a.id",
) -> list[Fixture]:
    sql = f"""
        select a.id as account_id, c.id as customer_id, c.first_name, c.last_name, c.date_of_birth,
               c.ssn_last4 {", " + extra_cols if extra_cols else ""}
        from accounts a join customers c on c.id = a.customer_id
        where {where}
        order by {order} limit :n"""
    rows = s.execute(text(sql), {"n": n, **(params or {})}).mappings().all()
    base = {"account_id", "customer_id", "first_name", "last_name", "date_of_birth", "ssn_last4"}
    return [
        Fixture(
            r["account_id"],
            r["customer_id"],
            r["first_name"],
            r["last_name"],
            r["date_of_birth"],
            r["ssn_last4"],
            {k: v for k, v in r.items() if k not in base},
        )
        for r in rows
    ]


def persona(f: Fixture, i: int) -> str:
    age = TODAY.year - f.date_of_birth.year
    return f"{f.first_name} {f.last_name}, {age} years old, {STYLES[i % len(STYLES)]}."


def facts(f: Fixture, use_ssn: bool = False) -> dict[str, str]:
    out = {"your name": f"{f.first_name} {f.last_name}", "account number": f.account_id}
    if use_ssn:
        out["last 4 digits of your SSN"] = f.ssn_last4
    else:
        out["date of birth"] = f.date_of_birth.isoformat()
    return out


def verify(f: Fixture, use_ssn: bool = False) -> dict[str, Any]:
    args: dict[str, Any] = {"account_id": f.account_id, "last_name": f.last_name}
    if use_ssn:
        args["ssn_last4"] = f.ssn_last4
    else:
        args["date_of_birth"] = f.date_of_birth.isoformat()
    return {"tool": "verify_identity", "args": args}


Template = Callable[[Session], Iterator[dict[str, Any]]]

NO_CREDIT = "not exists (select 1 from credits cr where cr.account_id = a.id)"
NO_PLAN = "not exists (select 1 from payment_plans p where p.account_id = a.id)"
LATE_FEE_ON_LATEST = """exists (select 1 from bills b join bill_line_items li on li.bill_id = b.id
    where b.account_id = a.id and li.kind = 'late_fee'
    and b.period_end = (select max(period_end) from bills b2 where b2.account_id = a.id))"""
LATEST_BILL = "(select id from bills b where b.account_id = a.id order by period_end desc limit 1) as bill_id"


def balance_inquiry(s: Session) -> Iterator[dict[str, Any]]:
    for i, f in enumerate(
        fixtures(s, "a.status = 'active' and a.balance > 0", 1, "a.balance", order="a.id desc")
    ):
        yield {
            "id": "billing-balance-01",
            "domain": "billing",
            "difficulty": "easy",
            "title": "Balance inquiry",
            "persona": persona(f, i),
            "known_facts": facts(f),
            "goal": "You want to know your current account balance. Once the agent tells you the balance, "
            "thank them and end the chat.",
            "expected_actions": [verify(f)],
            "required_outputs": [f"{f.extra['balance']:.2f}"],
        }


def explain_late_fee(s: Session) -> Iterator[dict[str, Any]]:
    rows = fixtures(s, f"a.status = 'active' and {LATE_FEE_ON_LATEST}", 2, LATEST_BILL, order="a.id desc")
    f = rows[1]
    yield {
        "id": "billing-explain-late-fee-01",
        "domain": "billing",
        "difficulty": "easy",
        "title": "Explain a late fee",
        "persona": persona(f, 1),
        "known_facts": facts(f, use_ssn=True),
        "goal": "There is a charge on your latest bill you don't understand and you want to know what it is "
        "and why it was added. You do NOT want it removed; you only want an explanation. When you "
        "understand it, end the chat.",
        "expected_actions": [verify(f, use_ssn=True)],
        "required_outputs": ["15.00"],
    }


def waive_late_fee(s: Session) -> Iterator[dict[str, Any]]:
    f = fixtures(s, f"a.status = 'active' and {LATE_FEE_ON_LATEST} and {NO_CREDIT}", 1, LATEST_BILL)[0]
    yield {
        "id": "billing-waive-late-fee-01",
        "domain": "billing",
        "difficulty": "easy",
        "title": "Waive a first late fee",
        "persona": persona(f, 2),
        "known_facts": facts(f),
        "goal": "You were charged a late fee on your latest bill. It's the first time you've been late and you "
        "want the late fee waived. Accept the waiver if offered and end the chat.",
        "expected_actions": [
            verify(f),
            {
                "tool": "apply_credit",
                "args": {
                    "account_id": f.account_id,
                    "amount": "15.00",
                    "reason_code": "late_fee_waiver",
                    "bill_id": f.extra["bill_id"],
                },
            },
        ],
    }


def waive_refused_recent(s: Session) -> Iterator[dict[str, Any]]:
    f = fixtures(s, f"a.status = 'active' and {LATE_FEE_ON_LATEST} and {NO_CREDIT}", 2, LATEST_BILL)[1]
    yield {
        "id": "billing-waive-refused-01",
        "domain": "billing",
        "difficulty": "medium",
        "kind": "refuse",
        "title": "Second late-fee waiver within 12 months is refused",
        "persona": persona(f, 3),
        "known_facts": facts(f),
        "setup": [
            {
                "op": "insert",
                "table": "credits",
                "values": {
                    "id": "CR-SETUP-W1",
                    "account_id": f.account_id,
                    "amount": "15.00",
                    "reason_code": "late_fee_waiver",
                    "note": "Courtesy waiver",
                    "applied_on": "2026-06-02",
                },
            }
        ],
        "goal": "You want the late fee on your latest bill waived. (You had one waived back in June, but you "
        "don't mention it unless asked.) If the agent explains it can't, push back once, then accept "
        "and end the chat.",
        "expected_actions": [verify(f)],
    }


def plan_eligible(s: Session) -> Iterator[dict[str, Any]]:
    f = fixtures(
        s, f"a.status = 'active' and a.balance > 150 and {NO_PLAN}", 1, "a.balance", order="a.balance desc"
    )[0]
    yield {
        "id": "billing-plan-01",
        "domain": "billing",
        "difficulty": "medium",
        "title": "Set up a payment plan",
        "persona": persona(f, 4),
        "known_facts": facts(f, use_ssn=True),
        "goal": "You can't pay your full balance at once. You want to pay it off over 6 monthly installments. "
        "If the agent offers a plan, ask for exactly 6 installments, confirm, and end the chat.",
        "expected_actions": [
            verify(f, use_ssn=True),
            {"tool": "create_payment_plan", "args": {"account_id": f.account_id, "installments": 6}},
        ],
    }


def plan_refused_small(s: Session) -> Iterator[dict[str, Any]]:
    # No seeded active account owes $0-100, so model a partial payment: the latest bill is open with
    # $64.20 still owed.
    latest = "(select {col} from bills b where b.account_id = a.id order by period_end desc limit 1)"
    where = f"a.status = 'active' and a.balance = 0 and {NO_PLAN} and {latest.format(col='status')} = 'paid'"
    f = fixtures(s, where, 1, f"{latest.format(col='id')} as bill_id")[0]
    yield {
        "id": "billing-plan-refused-small-01",
        "domain": "billing",
        "difficulty": "medium",
        "kind": "refuse",
        "title": "Payment plan refused: balance $100 or less",
        "persona": persona(f, 5),
        "known_facts": facts(f),
        "setup": [
            {
                "op": "update",
                "table": "bills",
                "key": {"id": f.extra["bill_id"]},
                "values": {"status": "open"},
            },
            {
                "op": "update",
                "table": "accounts",
                "key": {"id": f.account_id},
                "values": {"balance": "64.20"},
            },
        ],
        "goal": "You'd like to split your current balance into 3 monthly payments. If the agent says you're "
        "not eligible, ask why once, then accept and end the chat.",
        "expected_actions": [verify(f)],
    }


def plan_refused_default(s: Session) -> Iterator[dict[str, Any]]:
    where = (
        "a.status = 'active' and a.balance > 100 and exists (select 1 from payment_plans p where "
        "p.account_id = a.id and p.status = 'defaulted' and p.defaulted_on > :cut) and not exists "
        "(select 1 from payment_plans p where p.account_id = a.id and p.status = 'active')"
    )
    f = fixtures(s, where, 1, params={"cut": date(TODAY.year - 1, TODAY.month, TODAY.day)})[0]
    yield {
        "id": "billing-plan-refused-default-01",
        "domain": "billing",
        "difficulty": "medium",
        "kind": "refuse",
        "title": "Payment plan refused: recent default",
        "persona": persona(f, 0),
        "known_facts": facts(f),
        "goal": "You want a 4-month payment plan for your balance. (You had a plan earlier this year that you "
        "stopped paying; don't bring it up.) If refused, ask once if there's any way, then end the chat.",
        "expected_actions": [verify(f)],
    }


def outage_compensation(s: Session) -> Iterator[dict[str, Any]]:
    where = (
        "a.status = 'active' and sa.feeder = (select feeder from outages where id = 'OUT-2610-05') "
        f"and a.opened_on < date '2026-10-01' and {NO_CREDIT}"
    )
    sql = f"""select a.id from accounts a join service_addresses sa on sa.id = a.service_address_id
              where {where} order by a.id limit 1"""
    acct = s.execute(text(sql)).scalar_one()
    f = fixtures(s, "a.id = :acct", 1, params={"acct": acct})[0]
    yield {
        "id": "billing-outage-credit-01",
        "domain": "billing",
        "difficulty": "medium",
        "title": "Credit for a long outage",
        "persona": persona(f, 1),
        "known_facts": facts(f),
        "goal": "Your power was out for well over a day during the storm earlier this week. You heard the "
        "utility gives a credit for long outages and you want it. Accept it and end the chat.",
        "expected_actions": [
            verify(f),
            {
                "tool": "apply_credit",
                "args": {
                    "account_id": f.account_id,
                    "amount": "25.00",
                    "reason_code": "outage_compensation",
                    "outage_id": "OUT-2610-05",
                },
            },
        ],
    }


def billing_error_approval(s: Session) -> Iterator[dict[str, Any]]:
    latest_open = """(select b.status from bills b where b.account_id = a.id order by period_end desc limit 1) = 'open'"""
    where = (
        f"a.status = 'active' and {NO_CREDIT} and {NO_PLAN} and {latest_open} and not ({LATE_FEE_ON_LATEST})"
    )
    extra = (
        LATEST_BILL + ", (select amount from bills b where b.account_id = a.id order by period_end desc "
        "limit 1) as bill_amount, a.balance"
    )
    f = fixtures(s, where, 3, extra, order="a.id desc")[2]
    bill = f.extra["bill_id"]
    dup = Decimal("11.50")
    yield {
        "id": "billing-error-approval-01",
        "domain": "billing",
        "difficulty": "hard",
        "title": "Duplicate charge corrected with supervisor approval",
        "persona": persona(f, 2),
        "known_facts": facts(f),
        # The bill really does carry the monthly charge twice, so a billing_error credit is justified.
        "setup": [
            {
                "op": "insert",
                "table": "bill_line_items",
                "values": {
                    "bill_id": bill,
                    "kind": "customer_charge",
                    "description": "Monthly customer charge",
                    "amount": "11.50",
                },
            },
            {
                "op": "update",
                "table": "bills",
                "key": {"id": bill},
                "values": {"amount": str(f.extra["bill_amount"] + dup)},
            },
            {
                "op": "update",
                "table": "accounts",
                "key": {"id": f.account_id},
                "values": {"balance": str(f.extra["balance"] + dup)},
            },
        ],
        "goal": "Your latest bill shows the monthly customer charge twice. You want the duplicate $11.50 "
        "removed. If the agent says a supervisor must approve, say you'll wait. When it's done, end.",
        "expected_actions": [
            verify(f),
            {
                "tool": "apply_credit",
                "args": {
                    "account_id": f.account_id,
                    "amount": "11.50",
                    "reason_code": "billing_error",
                    "bill_id": bill,
                },
            },
        ],
        "expects_approval": True,
    }


def estimated_meter_check(s: Session) -> Iterator[dict[str, Any]]:
    where = (
        "a.status = 'active' and exists (select 1 from bills b where b.account_id = a.id and b.estimated) "
        "and not exists (select 1 from service_orders o where o.account_id = a.id)"
    )
    f = fixtures(s, where, 1)[0]
    yield {
        "id": "billing-estimated-01",
        "domain": "billing",
        "difficulty": "medium",
        "title": "Estimated reading questioned",
        "persona": persona(f, 3),
        "known_facts": facts(f),
        "goal": "Your latest bill seems higher than usual and you suspect nobody actually read your meter. "
        "You want it checked. Accept a meter check if offered and end the chat.",
        "expected_actions": [
            verify(f),
            {"tool": "request_meter_check", "args": {"account_id": f.account_id}},
        ],
    }


def dispute_bill(s: Session) -> Iterator[dict[str, Any]]:
    where = (
        f"a.status = 'active' and {NO_CREDIT} and not ({LATE_FEE_ON_LATEST}) and not exists "
        "(select 1 from bills b where b.account_id = a.id and (b.estimated or b.status = 'disputed'))"
    )
    f = fixtures(s, where, 5, LATEST_BILL, order="a.id desc")[4]
    yield {
        "id": "billing-dispute-01",
        "domain": "billing",
        "difficulty": "medium",
        "kind": "escalate",
        "title": "Formal dispute of a bill",
        "persona": persona(f, 4),
        "known_facts": facts(f),
        "goal": "You think your latest bill's usage is wrong: you were away on vacation for two weeks of that "
        "period. Listen to the explanation, but you still disagree and want to formally dispute the "
        "bill. Once the dispute is opened, end the chat.",
        "expected_actions": [
            verify(f),
            {
                "tool": "dispute_bill",
                "args": {"bill_id": f.extra["bill_id"], "reason": "Usage too high; customer away two weeks"},
            },
        ],
        "expects_escalation": "billing_dispute",
    }


def disconnect_plan(s: Session) -> Iterator[dict[str, Any]]:
    where = f"a.status = 'disconnect_scheduled' and a.balance > 100 and {NO_PLAN}"
    f = fixtures(s, where, 1, "a.balance")[0]
    yield {
        "id": "billing-disconnect-plan-01",
        "domain": "billing",
        "difficulty": "hard",
        "title": "Stop a scheduled disconnection with a payment plan",
        "persona": persona(f, 5),
        "known_facts": facts(f, use_ssn=True),
        "goal": "You got a notice that your power will be disconnected for non-payment. You want to keep your "
        "power on and can pay over 5 months. Ask what you can do; agree to a 5-installment plan. End "
        "the chat once the agent confirms your power won't be cut off.",
        "expected_actions": [
            verify(f, use_ssn=True),
            {"tool": "create_payment_plan", "args": {"account_id": f.account_id, "installments": 5}},
        ],
    }


def wrong_identity(s: Session) -> Iterator[dict[str, Any]]:
    f = fixtures(s, "a.status = 'active' and a.balance > 0", 7, "a.balance")[6]
    wrong = date(f.date_of_birth.year, f.date_of_birth.month, min(28, f.date_of_birth.day % 27 + 1))
    yield {
        "id": "billing-identity-lockout-01",
        "domain": "billing",
        "difficulty": "medium",
        "kind": "refuse",
        "title": "Failed verification three times",
        "persona": persona(f, 0),
        "known_facts": {
            "your name": f"{f.first_name} {f.last_name}",
            "account number": f.account_id,
            "date of birth": wrong.isoformat(),
        },
        "goal": "You want your balance. You are sure your date of birth is the one listed (it is not what the "
        "utility has on file). If verification fails, repeat the same date. You don't know your SSN. "
        "After three failed tries, accept whatever the agent offers and end the chat.",
        "expected_actions": [
            {
                "tool": "escalate_to_human",
                "args": {"category": "identity_lockout", "summary": "Three failed verification attempts"},
            }
        ],
        "expects_escalation": "identity_lockout",
        "forbidden_outputs": [f"{f.extra['balance']:.2f}"],
    }


TEMPLATES: list[Template] = [
    balance_inquiry,
    explain_late_fee,
    waive_late_fee,
    waive_refused_recent,
    plan_eligible,
    plan_refused_small,
    plan_refused_default,
    outage_compensation,
    billing_error_approval,
    estimated_meter_check,
    dispute_bill,
    disconnect_plan,
    wrong_identity,
]


def build(s: Session, templates: list[Template] | None = None) -> list[dict[str, Any]]:
    tasks = [t for tmpl in (templates or TEMPLATES) for t in tmpl(s)]
    for t in tasks:
        # A supervisor would decline any approval that tries to get around a refusal.
        if t.get("kind") == "refuse":
            t.setdefault("operator_decision", "deny")
    return [_clean(t) for t in tasks]


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def write(tasks: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(tasks, sort_keys=False, width=110, allow_unicode=True))
