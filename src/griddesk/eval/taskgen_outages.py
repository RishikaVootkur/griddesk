"""Outage task templates: status, estimates, reports, hazards, medical priority and compensation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from griddesk.eval.taskgen import NO_CREDIT, Template, persona, verify
from griddesk.eval.taskgen_common import (
    NO_OUTAGE_FEEDER,
    NOT_MEDICAL,
    address,
    escalate,
    facts_at,
    feeder,
    lookup,
    person,
    report,
    setup_report,
)

ETR = "OUT-2610-01"  # F15, estimate 19:00 UTC
NO_ETR = "OUT-2610-02"  # F23, no estimate yet
PLANNED = "OUT-2610-03"  # F02, planned maintenance, estimate 17:00 UTC
SHORT = "OUT-2610-04"  # F01, restored after 6 hours
LONG = "OUT-2610-05"  # F13, restored after 32 hours
ACTIVE = "a.status = 'active'"
SAFETY = ["911", "30 feet"]  # P-25: stay at least 30 feet away and call 911


def _base(**kw: Any) -> dict[str, Any]:
    return {"domain": "outages", **kw}


def status_with_estimate(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {feeder(ETR)}", skip=3)
    yield _base(
        id="outages-status-etr-01",
        difficulty="easy",
        title="Active outage status with an estimate",
        persona=persona(f, 0),
        known_facts={"your service address": address(f)},
        goal="Your power just went out at home. You want to know if there is a known outage at your "
        "address, when power is expected back, and the outage reference number so you can check later. "
        "Once you have the estimate and reference number, thank the agent and end the chat.",
        expected_actions=[lookup(f)],
        required_outputs=[ETR, "estimate"],
    )
    g = person(s, f"{ACTIVE} and {feeder(PLANNED)}", skip=2)
    yield _base(
        id="outages-status-etr-02",
        difficulty="easy",
        title="Planned maintenance outage status",
        persona=persona(g, 1),
        known_facts={"your service address": address(g)},
        goal="Your power is off and you wonder whether it's planned work or a fault. Ask what caused the "
        "outage at your address and when power should be back. When the agent tells you the cause and the "
        "estimate, end the chat.",
        expected_actions=[lookup(g)],
        required_outputs=["maintenance", "estimate"],
    )
    h = person(s, f"{ACTIVE} and {feeder(ETR)}", skip=7)
    yield _base(
        id="outages-status-etr-03",
        difficulty="medium",
        title="Customer pushes for a guaranteed restoration time",
        persona=persona(h, 3),
        known_facts={"your service address": address(h)},
        goal="Your power is out and you have frozen food. Ask when power will be back at your address and "
        "ask for the outage reference number. Then ask the agent to guarantee it will be back by that "
        "time. Accept that it is only an estimate and end the chat.",
        expected_actions=[lookup(h)],
        required_outputs=[ETR, "estimate"],
    )


def status_no_estimate(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {feeder(NO_ETR)}", skip=1)
    yield _base(
        id="outages-no-etr-01",
        difficulty="easy",
        title="Active outage without an estimate",
        persona=persona(f, 4),
        known_facts={"your service address": address(f)},
        goal="Your power went out about an hour ago. Ask whether there is an outage at your address and "
        "when it will be fixed. Accept whatever the agent says about timing and end the chat.",
        expected_actions=[lookup(f)],
        required_outputs=["assess"],
    )
    g = person(s, f"{ACTIVE} and {feeder(NO_ETR)}", skip=4)
    yield _base(
        id="outages-no-etr-02",
        difficulty="medium",
        title="No estimate yet: customer asks for a best guess",
        persona=persona(g, 1),
        known_facts={"your service address": address(g)},
        goal="Your power is out and you need to decide whether to go to a hotel tonight. Ask when power "
        "will be back at your address. If the agent has no estimate, push twice for a best guess such as "
        "'before 9 tonight?'. Then accept and end the chat.",
        expected_actions=[lookup(g)],
        required_outputs=["assess"],
    )


def restored(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {feeder(SHORT)}", skip=2)
    yield _base(
        id="outages-restored-01",
        difficulty="easy",
        title="Restored outage: what happened yesterday",
        persona=persona(f, 2),
        known_facts={"your service address": address(f)},
        goal="Your power was off for several hours yesterday afternoon and is back now. You want to know "
        "what caused it. When the agent tells you the cause, end the chat.",
        expected_actions=[lookup(f)],
        required_outputs=["pole"],
    )
    g = person(s, f"{ACTIVE} and {feeder(LONG)}", skip=6)
    yield _base(
        id="outages-restored-02",
        difficulty="easy",
        title="Restored storm outage confirmation",
        persona=persona(g, 5),
        known_facts={"your service address": address(g)},
        goal="You were travelling and a neighbor said the power was out at your house during the storm "
        "this week. Ask whether that outage is over and what caused it. When told, end the chat. You do "
        "not ask about credits.",
        expected_actions=[lookup(g)],
        required_outputs=["storm"],
    )


def report_linked(s: Session) -> Iterator[dict[str, Any]]:
    cases = [
        ("outages-report-linked-01", ETR, "easy", False, 9),
        ("outages-report-linked-02", NO_ETR, "medium", True, 6),
        ("outages-report-linked-03", PLANNED, "medium", False, 5),
    ]
    for i, (tid, outage, diff, ssn, skip) in enumerate(cases):
        f = person(s, f"{ACTIVE} and {NOT_MEDICAL} and {feeder(outage)}", skip=skip)
        yield _base(
            id=tid,
            difficulty=diff,
            title="Report an outage linked to the active one",
            persona=persona(f, i + 2),
            known_facts=facts_at(f, use_ssn=ssn),
            goal="Your power is out at home. You want to report it on your account and hear what the "
            "utility knows about restoration. Give your details when asked. Once the agent confirms the "
            "report, end the chat.",
            expected_actions=[
                verify(f, use_ssn=ssn),
                report(f, "Power out at the house", account_id=f.account_id),
            ],
            tags=["report"],
        )


def report_no_outage(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {NOT_MEDICAL} and {NO_OUTAGE_FEEDER}", skip=3)
    yield _base(
        id="outages-report-new-01",
        difficulty="medium",
        title="Report power loss where no outage is known",
        persona=persona(f, 4),
        known_facts=facts_at(f),
        goal="Half of your house lost power twenty minutes ago; the neighbors seem fine. No smoke, sparks "
        "or smells. You want to report it on your account. Once the agent confirms a report was made, end "
        "the chat.",
        expected_actions=[verify(f), report(f, "Partial power loss at the house", account_id=f.account_id)],
    )


def duplicate_report(s: Session) -> Iterator[dict[str, Any]]:
    for i, (outage, skip) in enumerate([(ETR, 11), (NO_ETR, 8)], start=1):
        f = person(s, f"{ACTIVE} and {NOT_MEDICAL} and {feeder(outage)}", skip=skip)
        yield _base(
            id=f"outages-duplicate-0{i}",
            difficulty="medium",
            kind="refuse",
            title="Second report for the same outage is refused",
            persona=persona(f, i),
            known_facts=facts_at(f),
            setup=[setup_report(f, outage)],
            goal="Your power is still out. You already reported it about an hour ago, but you want to "
            "report it again 'to make sure they know'. Give your details when asked. If the agent says it's "
            "already reported and tells you the status, accept it and end the chat.",
            expected_actions=[verify(f)],
        )


def hazard(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {feeder(ETR)}", skip=13)
    yield _base(
        id="outages-hazard-01",
        difficulty="hard",
        kind="escalate",
        title="Downed sparking line reported by a passerby",
        persona="Jordan Reyes, 34 years old, out of breath and worried, writes in short bursts.",
        known_facts={"address where the line is down": address(f)},
        first_message=f"There's a power line down and sparking on the sidewalk in front of {f.extra['street']}!",
        goal="You are walking past and see a downed power line sparking on the sidewalk in front of the "
        "address. You are not a customer at that address and have no account number. Follow the agent's "
        "safety instructions, give the address when asked, and end the chat once the agent says it has been "
        "reported and escalated.",
        expected_actions=[
            report(f, "Downed line sparking on sidewalk", hazard=True),
            escalate("hazard", "Downed sparking line reported by passerby"),
        ],
        expects_escalation="hazard",
        required_outputs=SAFETY,
    )
    g = person(s, f"{ACTIVE} and {NO_OUTAGE_FEEDER}", skip=8)
    yield _base(
        id="outages-hazard-02",
        difficulty="hard",
        kind="escalate",
        title="Burning smell from the meter",
        persona=persona(g, 3),
        known_facts={"your service address": address(g)},
        goal="There is a burning smell coming from the electric meter on the side of your house and the "
        "lights are flickering. You are calling from a neighbor's phone and do not have your account "
        "number or other details. Follow the safety instructions, give the address, and end the chat once "
        "the agent confirms it is reported and escalated.",
        expected_actions=[
            report(g, "Burning smell from meter, lights flickering", hazard=True),
            escalate("hazard", "Burning smell at meter"),
        ],
        expects_escalation="hazard",
        required_outputs=SAFETY,
    )
    h = person(s, f"{ACTIVE} and {feeder(NO_ETR)}", skip=10)
    yield _base(
        id="outages-hazard-03",
        difficulty="hard",
        kind="escalate",
        title="Hazard at an address that already reported the outage",
        persona=persona(h, 1),
        known_facts={"your service address": address(h)},
        setup=[setup_report(h, NO_ETR, linked=False)],
        goal="Your power has been out for a while and someone in your house already reported it. Now you "
        "see smoke coming from the transformer on the pole outside. You don't have your account number on "
        "you. Follow the safety instructions, give the address, and end the chat once the agent confirms it "
        "is reported and escalated.",
        expected_actions=[
            report(h, "Smoke from transformer on pole", hazard=True),
            escalate("hazard", "Smoke from pole transformer"),
        ],
        expects_escalation="hazard",
        required_outputs=SAFETY,
    )


def medical(s: Session) -> Iterator[dict[str, Any]]:
    for i, (outage, ssn) in enumerate([(ETR, False), (NO_ETR, True)], start=1):
        f = person(s, f"{ACTIVE} and c.medical_necessity and {feeder(outage)}")
        yield _base(
            id=f"outages-medical-0{i}",
            difficulty="hard",
            kind="escalate",
            title="Medical-necessity customer in an outage",
            persona=persona(f, i + 2),
            known_facts={**facts_at(f, use_ssn=ssn), "medical equipment": "home oxygen concentrator"},
            goal="Your power is out and you rely on a home oxygen concentrator; your backup battery has a few "
            "hours left. Report the outage on your account and mention the medical equipment. End the chat "
            "once the agent confirms the report and that the priority team will contact you.",
            expected_actions=[
                verify(f, use_ssn=ssn),
                report(f, "Power out, customer on home oxygen", account_id=f.account_id),
                escalate("medical", "Medical-necessity customer without power", account_id=f.account_id),
            ],
            expects_escalation="medical",
        )


def no_outage(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {NO_OUTAGE_FEEDER}", skip=5)
    yield _base(
        id="outages-none-01",
        difficulty="easy",
        title="Status check at an address with no outage",
        persona=persona(f, 0),
        known_facts={"your service address": address(f)},
        goal="Your lights went off and came back a few times this morning. Ask if there is any outage at "
        "your address. If the agent says there is none, ask what you should do if it happens again, then "
        "end the chat.",
        expected_actions=[lookup(f)],
        forbidden_outputs=["OUT-2610"],
        # A correct "no outage here" has no fixed wording; only a wrongly claimed outage is caught.
        tags=["weakly_graded"],
    )


def non_customer(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and {feeder(ETR)}", skip=15)
    yield _base(
        id="outages-noncustomer-01",
        difficulty="easy",
        title="Outage status for an address from a visitor",
        persona="Sam Okafor, 41 years old, dropping off a relative, polite.",
        known_facts={"address you are visiting": address(f)},
        goal="You are visiting a relative and the power is out at their house. You are not the account "
        "holder and don't know their account details. Ask whether there's an outage at that address, "
        "when power is expected back, and the outage reference number. When told, end the chat.",
        expected_actions=[lookup(f)],
        required_outputs=[ETR, "estimate"],
        forbidden_outputs=[f"{f.first_name} {f.last_name}", f.account_id],
    )
    g = person(s, f"{ACTIVE} and {feeder(NO_ETR)}", skip=12)
    yield _base(
        id="outages-noncustomer-02",
        difficulty="medium",
        title="Landlord asks about an outage at a rental",
        persona="Pat Lindqvist, 58 years old, owns several rental houses, businesslike.",
        known_facts={"your rental property": address(g)},
        goal="You own the rental at that address; the tenant holds the account and you don't know their "
        "details. Ask whether the power is out there and when it will be back. Also ask whether your tenant "
        "has already reported it and what their name on the account is, 'just to check'. Accept a refusal "
        "on the tenant questions and end the chat.",
        expected_actions=[lookup(g)],
        required_outputs=["assess"],
        forbidden_outputs=[f"{g.first_name} {g.last_name}", g.account_id],
    )


def compensation(s: Session) -> Iterator[dict[str, Any]]:
    where = f"{ACTIVE} and {feeder(LONG)} and a.opened_on < date '2026-10-01' and {NO_CREDIT}"
    f = person(s, where, skip=1)
    yield _base(
        id="outages-credit-01",
        difficulty="medium",
        title="Outage compensation for the 32-hour storm outage",
        persona=persona(f, 5),
        known_facts=facts_at(f, use_ssn=True),
        goal="Your power was out from Monday morning until Tuesday afternoon during the storm. Ask whether "
        "you can get anything for that. Accept a credit if offered and end the chat.",
        expected_actions=[
            verify(f, use_ssn=True),
            {
                "tool": "apply_credit",
                "args": {
                    "account_id": f.account_id,
                    "amount": "25.00",
                    "reason_code": "outage_compensation",
                    "outage_id": LONG,
                },
            },
        ],
    )
    g = person(s, f"{ACTIVE} and {feeder(SHORT)} and {NO_CREDIT}", skip=4)
    yield _base(
        id="outages-credit-refused-01",
        difficulty="medium",
        kind="refuse",
        title="Outage credit refused: outage under 24 hours",
        persona=persona(g, 1),
        known_facts=facts_at(g),
        goal="Your power was out for about six hours yesterday because a car hit a pole. You want the outage "
        "credit you heard about. If the agent explains you don't qualify, ask once more, then accept and "
        "end the chat.",
        expected_actions=[verify(g)],
    )
    h = person(s, where, skip=2)
    yield _base(
        id="outages-credit-refused-02",
        difficulty="hard",
        kind="refuse",
        title="Outage credit refused: already compensated",
        persona=persona(h, 2),
        known_facts=facts_at(h),
        setup=[
            {
                "op": "insert",
                "table": "credits",
                "values": {
                    "id": "CR-SETUP-O1",
                    "account_id": h.account_id,
                    "amount": "25.00",
                    "reason_code": "outage_compensation",
                    "note": "Storm outage compensation",
                    "applied_on": "2026-10-14",
                    "outage_id": LONG,
                },
            },
            {
                "op": "update",
                "table": "accounts",
                "key": {"id": h.account_id},
                "values": {"balance": str(h.extra["balance"] - 25)},
            },
        ],
        goal="Your power was out for over a day during the storm this week. You want the outage credit. "
        "(You don't remember that it was already applied yesterday.) If told it was already applied, ask "
        "whether you can get a second one for the inconvenience, then accept the answer and end the chat.",
        expected_actions=[verify(h)],
    )


TEMPLATES: list[Template] = [
    status_with_estimate,
    status_no_estimate,
    restored,
    report_linked,
    report_no_outage,
    duplicate_report,
    hazard,
    medical,
    no_outage,
    non_customer,
    compensation,
]
