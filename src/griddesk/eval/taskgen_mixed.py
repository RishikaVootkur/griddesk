"""Multi-intent task templates: two or three requests in one conversation, across specialists."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from griddesk.eval.taskgen import (
    LATE_FEE_ON_LATEST,
    NO_CREDIT,
    NO_PLAN,
    Fixture,
    Template,
    facts,
    persona,
    verify,
)
from griddesk.eval.taskgen_accounts import new_email
from griddesk.eval.taskgen_common import (
    NO_ORDERS,
    NO_PAST_DUE,
    NOT_MEDICAL,
    SINGLE_ACCOUNT,
    address,
    escalate,
    facts_at,
    feeder,
    lookup,
    person,
    report,
    vacant,
)
from griddesk.eval.taskgen_outages import ETR, LONG, NO_ETR
from griddesk.tools.service import new_account_id

ACTIVE = "a.status = 'active'"


def _base(**kw: Any) -> dict[str, Any]:
    return {"domain": "mixed", **kw}


def _latest_bill(s: Session, account_id: str) -> str:
    sql = "select id from bills where account_id = :a order by period_end desc limit 1"
    return str(s.execute(text(sql), {"a": account_id}).scalar_one())


def _act(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args}


def _money(f: Fixture) -> str:
    return f"{f.extra['balance']:.2f}"


def outage_then_billing(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and a.balance > 0 and {feeder(ETR)}", skip=17)
    yield _base(
        id="mixed-outage-balance-01",
        difficulty="medium",
        title="Outage status, then the account balance",
        persona=persona(f, 2),
        known_facts=facts_at(f),
        goal="Your power is out. First ask whether there's an outage at your address, when it should be "
        "back, and the outage reference number. Then ask what your current balance is. When you have the "
        "estimate, reference number and balance, end the chat.",
        expected_actions=[lookup(f), verify(f)],
        required_outputs=[ETR, _money(f)],
    )
    g = person(s, f"{ACTIVE} and a.autopay and {feeder(NO_ETR)}", skip=2)
    yield _base(
        id="mixed-noetr-autopay-off-01",
        difficulty="medium",
        title="Outage status, then turn off autopay",
        persona=persona(g, 4),
        known_facts=facts_at(g, use_ssn=True),
        goal="Ask whether the power outage at your address has an estimated fix time. Then, while you have "
        "the agent, turn off autopay because you're changing banks. End once autopay is confirmed off.",
        expected_actions=[
            lookup(g),
            verify(g, use_ssn=True),
            _act("set_autopay", account_id=g.account_id, enabled=False),
        ],
        required_outputs=["assess"],
    )


def hazard_then_balance(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and a.balance > 0 and {feeder(ETR)}", skip=19)
    yield _base(
        id="mixed-hazard-balance-01",
        difficulty="hard",
        kind="escalate",
        title="Sparking line at home, then a balance question",
        persona=persona(f, 1),
        known_facts=facts_at(f),
        first_message="The wire from the pole to my house is down in the yard and it's sparking.",
        goal="A service wire is down and sparking in your yard. Do not give account details until the agent "
        "has handled the hazard; give your address when asked. Once the agent says it's reported and "
        "escalated, say you're safely inside, and ask for your account balance, giving your details. "
        "When told the balance, end the chat.",
        expected_actions=[
            report(f, "Service drop down and sparking in yard", hazard=True),
            escalate("hazard", "Downed sparking service wire"),
            verify(f),
        ],
        expects_escalation="hazard",
        required_outputs=[_money(f)],
    )


def report_and_autopay(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {NOT_MEDICAL} and not a.autopay and {feeder(NO_ETR)}", skip=14)
    yield _base(
        id="mixed-report-autopay-01",
        difficulty="medium",
        title="Report an outage and ask to enroll in autopay",
        persona=persona(f, 0),
        known_facts=facts_at(f),
        goal="Your power is out; report it on your account. Then ask the agent to sign you up for autopay. "
        "If told autopay has to be set up online, accept. End the chat after both are answered.",
        expected_actions=[verify(f), report(f, "Power out at the house", account_id=f.account_id)],
        required_outputs=["portal"],
    )


def credit_and_phone(s: Session) -> Iterator[dict[str, Any]]:
    where = f"{ACTIVE} and {feeder(LONG)} and a.opened_on < date '2026-10-01' and {NO_CREDIT}"
    f = person(s, where, skip=3)
    phone = "(781) 555-0177"
    yield _base(
        id="mixed-credit-phone-01",
        difficulty="medium",
        title="Storm outage credit and a phone number update",
        persona=persona(f, 3),
        known_facts={**facts_at(f), "new phone number": phone},
        goal="You were without power for more than a day in the storm this week and want any credit you're "
        f"owed. Also update your phone number to {phone}. End once both are confirmed.",
        expected_actions=[
            verify(f),
            _act(
                "apply_credit",
                account_id=f.account_id,
                amount="25.00",
                reason_code="outage_compensation",
                outage_id=LONG,
            ),
            _act("update_contact_info", account_id=f.account_id, phone=phone),
        ],
    )


def billing_and_account(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and a.balance > 150 and {NO_PLAN}", skip=3, order="a.balance desc")
    email = new_email(f)
    yield _base(
        id="mixed-email-plan-01",
        difficulty="hard",
        title="Update email, then set up a payment plan",
        persona=persona(f, 5),
        known_facts={**facts(f), "new email": email},
        goal=f"First change your email to {email}. Then ask about paying your balance in 4 monthly "
        "installments and agree to a 4-installment plan. End once both are confirmed.",
        expected_actions=[
            verify(f),
            _act("update_contact_info", account_id=f.account_id, email=email),
            _act("create_payment_plan", account_id=f.account_id, installments=4),
        ],
    )
    g = person(s, f"{ACTIVE} and not a.paperless and {LATE_FEE_ON_LATEST} and {NO_CREDIT}", skip=1)
    yield _base(
        id="mixed-waive-paperless-01",
        difficulty="medium",
        title="Waive a first late fee and go paperless",
        persona=persona(g, 2),
        known_facts=facts(g),
        goal="You missed a payment because the paper bill got lost in the mail. Ask for the late fee on your "
        "latest bill to be waived (it's your first time) and switch to paperless billing. End once both "
        "are confirmed.",
        expected_actions=[
            verify(g),
            _act(
                "apply_credit",
                account_id=g.account_id,
                amount="15.00",
                reason_code="late_fee_waiver",
                bill_id=_latest_bill(s, g.account_id),
            ),
            _act("set_paperless", account_id=g.account_id, enabled=True),
        ],
    )
    h = person(s, f"{ACTIVE} and a.autopay", skip=30)
    mail = "PO Box 902, Fairhaven Heights, MA 01906"
    yield _base(
        id="mixed-autopay-mailing-01",
        difficulty="medium",
        title="Turn off autopay and set a mailing address",
        persona=persona(h, 4),
        known_facts={**facts(h, use_ssn=True), "new mailing address": mail},
        goal=f"You want autopay turned off and your mail sent to {mail}. End once both are confirmed.",
        expected_actions=[
            verify(h, use_ssn=True),
            _act("set_autopay", account_id=h.account_id, enabled=False),
            _act("update_contact_info", account_id=h.account_id, mailing_address=mail),
        ],
    )
    est = f"{ACTIVE} and exists (select 1 from bills b where b.account_id = a.id and b.estimated) and {NO_ORDERS}"
    k = person(s, est, skip=2)
    email = new_email(k, "work")
    yield _base(
        id="mixed-meter-email-01",
        difficulty="medium",
        title="Question an estimated bill and update email",
        persona=persona(k, 1),
        known_facts={**facts(k), "new email": email},
        goal="Your latest bill looks high and you think the meter wasn't actually read. Ask for it to be "
        f"checked and accept a meter check if offered. Also change your email to {email}. End once both "
        "are done.",
        expected_actions=[
            verify(k),
            _act("request_meter_check", account_id=k.account_id),
            _act("update_contact_info", account_id=k.account_id, email=email),
        ],
    )
    disp = (
        f"{ACTIVE} and {NO_CREDIT} and not ({LATE_FEE_ON_LATEST}) and not exists "
        "(select 1 from bills b where b.account_id = a.id and (b.estimated or b.status = 'disputed'))"
    )
    m = person(s, disp, skip=9, order="a.id desc")
    phone = "508-555-0190"
    yield _base(
        id="mixed-dispute-phone-01",
        difficulty="hard",
        kind="escalate",
        title="Dispute a bill and update the phone number",
        persona=persona(m, 0),
        known_facts={**facts(m), "new phone number": phone},
        goal="You believe your latest bill's usage is wrong because the house was empty for three weeks. "
        "Listen to the explanation but still dispute the bill formally. Also update your phone number to "
        f"{phone}. End once the dispute is opened and the phone is updated.",
        expected_actions=[
            verify(m),
            _act(
                "dispute_bill",
                bill_id=_latest_bill(s, m.account_id),
                reason="House empty three weeks; usage too high",
            ),
            _act("update_contact_info", account_id=m.account_id, phone=phone),
        ],
        expects_escalation="billing_dispute",
    )
    two = (
        f"{ACTIVE} and not a.paperless and exists (select 1 from accounts a5 where a5.customer_id = c.id "
        "and a5.id <> a.id and a5.status = 'active' and not a5.paperless)"
    )
    n = person(s, two)
    other = s.execute(
        text(
            "select id from accounts where customer_id = :c and id <> :a and status = 'active' order by id limit 1"
        ),
        {"c": n.customer_id, "a": n.account_id},
    ).scalar_one()
    yield _base(
        id="mixed-two-accounts-01",
        difficulty="hard",
        title="Paperless on both of a customer's accounts",
        persona=persona(n, 3),
        known_facts={**facts(n), "your other account number": str(other)},
        goal="You have two accounts (a home and a rental unit you pay for). You want paperless billing on "
        "both. Verify with the first account. End once the agent confirms both are paperless.",
        expected_actions=[
            verify(n),
            _act("set_paperless", account_id=n.account_id, enabled=True),
            _act("set_paperless", account_id=str(other), enabled=True),
        ],
    )


def service_and_more(s: Session) -> Iterator[dict[str, Any]]:
    where = f"{ACTIVE} and {NO_ORDERS} and {NO_PAST_DUE} and {SINGLE_ACCOUNT}"
    f = person(s, where, skip=110)
    a = vacant(s, skip=8)[0]
    new_id = new_account_id(f.customer_id, a["address_id"])
    yield _base(
        id="mixed-move-paperless-01",
        difficulty="hard",
        title="Move service and go paperless at the new address",
        persona=persona(f, 2),
        known_facts={**facts_at(f), "new address": address(a), "move date": "2026-11-21"},
        goal="You are moving on 2026-11-21. You want service moved from your current address to the new one "
        "on that date (wait if a supervisor must approve), and you want paperless billing on the new "
        "account. End once both are confirmed.",
        expected_actions=[
            verify(f),
            _act("lookup_address", street=a["street"], zip_code=a["zip"]),
            _act(
                "transfer_service",
                account_id=f.account_id,
                new_service_address_id=a["address_id"],
                move_date="2026-11-21",
            ),
            _act("set_paperless", account_id=new_id, enabled=True),
        ],
        expects_approval=True,
    )
    g = person(s, f"{ACTIVE} and a.balance > 0 and {NO_ORDERS}", skip=120)
    yield _base(
        id="mixed-stop-balance-01",
        difficulty="hard",
        title="Stop service and ask for the balance",
        persona=persona(g, 5),
        known_facts={**facts_at(g), "stop date": "2026-11-01"},
        goal="You are moving in with family. Ask what your current balance is, then ask to stop service on "
        "2026-11-01 (wait for approval if needed). End once you know the balance and the stop is confirmed.",
        expected_actions=[verify(g), _act("stop_service", account_id=g.account_id, stop_date="2026-11-01")],
        expects_approval=True,
        required_outputs=[_money(g)],
    )
    h = person(s, f"{ACTIVE} and {NO_ORDERS}", skip=130)
    email = new_email(h, "new")
    yield _base(
        id="mixed-stop-far-email-01",
        difficulty="hard",
        kind="refuse",
        title="Stop date too far out, but the email update goes through",
        persona=persona(h, 0),
        known_facts={**facts_at(h), "stop date": "2027-02-15", "new email": email},
        goal=f"Change your email to {email}, and schedule service to stop on 2027-02-15 when your lease "
        "ends. You do not want an earlier date. If the stop can't be scheduled that far out, accept it. End "
        "once both are answered.",
        expected_actions=[verify(h), _act("update_contact_info", account_id=h.account_id, email=email)],
    )
    pd = (
        f"{ACTIVE} and a.balance > 100 and {SINGLE_ACCOUNT} and {NO_PLAN} and exists "
        "(select 1 from bills b where b.account_id = a.id and b.status = 'past_due')"
    )
    k = person(s, pd)
    b = vacant(s, skip=9)[0]
    yield _base(
        id="mixed-plan-start-01",
        difficulty="hard",
        title="Payment plan clears the way to start service at a second address",
        persona=persona(k, 4),
        known_facts={**facts(k), "second address": address(b), "start date": "2026-11-10"},
        goal="You want service started at a second address (a workshop) on 2026-11-10 while keeping your "
        "current account. If told your past-due balance blocks it, agree to a 6-installment payment plan, "
        "then ask again to start the service. End once the start is confirmed.",
        expected_actions=[
            verify(k),
            _act("create_payment_plan", account_id=k.account_id, installments=6),
            _act("lookup_address", street=b["street"], zip_code=b["zip"]),
            _act("start_service", service_address_id=b["address_id"], start_date="2026-11-10"),
        ],
    )
    m = person(s, f"{ACTIVE} and not a.paperless and c.email is not null", skip=140)
    email = new_email(m, "family")
    yield _base(
        id="mixed-readback-update-01",
        difficulty="medium",
        title="Check the email on file, change it and go paperless",
        persona=persona(m, 1),
        known_facts={**facts(m), "new email": email},
        goal="Ask which email is on file. Whatever it is, change it to your new email and turn on paperless "
        "billing. End once both are confirmed.",
        expected_actions=[
            verify(m),
            _act("update_contact_info", account_id=m.account_id, email=email),
            _act("set_paperless", account_id=m.account_id, enabled=True),
        ],
        forbidden_outputs=[m.extra["email"]],
    )


TEMPLATES: list[Template] = [
    outage_then_billing,
    hazard_then_balance,
    report_and_autopay,
    credit_and_phone,
    billing_and_account,
    service_and_more,
]
