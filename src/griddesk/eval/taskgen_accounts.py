"""Account task templates: contact details, paperless and autopay, starting, stopping and moving service."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy.orm import Session

from griddesk.eval.taskgen import Fixture, Template, facts, persona, verify
from griddesk.eval.taskgen_common import (
    NO_ORDERS,
    NO_PAST_DUE,
    SINGLE_ACCOUNT,
    address,
    facts_at,
    person,
    vacant,
)
from griddesk.tools.service import new_account_id

ACTIVE = "a.status = 'active'"
STOP_DATE = "2026-10-31"
MOVE_DATE = "2026-11-14"
START_DATE = "2026-11-02"


def _base(**kw: Any) -> dict[str, Any]:
    return {"domain": "accounts", **kw}


def _contact(f: Fixture, **fields: str) -> dict[str, Any]:
    return {"tool": "update_contact_info", "args": {"account_id": f.account_id, **fields}}


def _flag(tool: str, f: Fixture, enabled: bool) -> dict[str, Any]:
    return {"tool": tool, "args": {"account_id": f.account_id, "enabled": enabled}}


def _lookup_address(a: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "lookup_address", "args": {"street": a["street"], "zip_code": a["zip"]}}


def _phone_tail(p: str) -> str:
    return p[-8:]  # "555-0122": matches the number however the area code is written


def new_email(f: Fixture, tag: str = "home") -> str:
    return f"{f.first_name.lower()}.{f.last_name.lower()}.{tag}@example.net"


def contact_updates(s: Session) -> Iterator[dict[str, Any]]:
    for i, ssn in enumerate([False, True], start=1):
        f = person(s, ACTIVE, skip=20 + i)
        email = new_email(f)
        yield _base(
            id=f"accounts-email-0{i}",
            difficulty="easy",
            title="Update the email address",
            persona=persona(f, i),
            known_facts={**facts(f, use_ssn=ssn), "new email": email},
            goal="You changed email providers and want your account to use your new email address. Give it "
            "when asked. Once the agent confirms the change, end the chat.",
            expected_actions=[verify(f, use_ssn=ssn), _contact(f, email=email)],
        )
    for i, phone in enumerate(["(617) 555-0148", "978-555-0163"], start=1):
        f = person(s, ACTIVE, skip=30 + i)
        yield _base(
            id=f"accounts-phone-0{i}",
            difficulty="easy",
            title="Update the phone number",
            persona=persona(f, i + 2),
            known_facts={**facts(f, use_ssn=i == 2), "new phone number": phone},
            goal="You got a new mobile number and want it on your account. Once the agent confirms the "
            "change, end the chat.",
            expected_actions=[verify(f, use_ssn=i == 2), _contact(f, phone=phone)],
        )
    for i, mail in enumerate(
        ["PO Box 4471, Cedar Junction, MA 01903", "18 Harbor Lane, Apt 3, Salem, MA 01970"], 1
    ):
        f = person(s, ACTIVE, skip=40 + i)
        yield _base(
            id=f"accounts-mailing-0{i}",
            difficulty="easy",
            title="Set a separate mailing address",
            persona=persona(f, i + 4),
            known_facts={**facts(f), "new mailing address": mail},
            goal="You want your paper mail from the utility sent to a different mailing address (your "
            "service address stays the same). Once the agent confirms the change, end the chat.",
            expected_actions=[verify(f), _contact(f, mailing_address=mail)],
        )
    f = person(s, ACTIVE, skip=50)
    good = new_email(f, "mail")
    bad = good.replace("@example.net", "@example")
    yield _base(
        id="accounts-email-invalid-01",
        difficulty="medium",
        title="Invalid email corrected on the second try",
        persona=persona(f, 0),
        known_facts={**facts(f), "the email you type first": bad, "your correct new email": good},
        goal=f"You want to change your email. First give it as {bad} (you forgot the .net). If the agent "
        f"says it isn't valid, apologise and give {good}. Once the change is confirmed, end the chat.",
        expected_actions=[verify(f), _contact(f, email=good)],
    )
    g = person(s, ACTIVE, skip=52)
    yield _base(
        id="accounts-phone-invalid-01",
        difficulty="medium",
        kind="refuse",
        title="Phone number with too few digits is refused",
        persona=persona(g, 5),
        known_facts={**facts(g), "the new phone number as you remember it": "(617) 555-014"},
        goal="You want to change your phone number to (617) 555-014. You are sure that is the whole number. "
        "If the agent says it isn't valid, say you'll check the number and call back, and end the chat.",
        expected_actions=[verify(g)],
    )
    h = person(s, ACTIVE, skip=54)
    yield _base(
        id="accounts-email-unverified-01",
        difficulty="easy",
        kind="refuse",
        title="Email change refused without an account number",
        persona=persona(h, 1),
        known_facts={
            "your name": f"{h.first_name} {h.last_name}",
            "your service address": address(h),
            "new email": new_email(h),
        },
        goal="You want to change the email on your account but you don't have your account number and "
        "can't find a bill. If the agent says it can't make changes without verifying you, ask where to "
        "find the account number, then end the chat.",
        expected_actions=[],
    )


def flags(s: Session) -> Iterator[dict[str, Any]]:
    for i in (1, 2):
        f = person(s, f"{ACTIVE} and not a.paperless", skip=10 + i)
        yield _base(
            id=f"accounts-paperless-on-0{i}",
            difficulty="easy",
            title="Turn on paperless billing",
            persona=persona(f, i),
            known_facts=facts(f, use_ssn=i == 2),
            goal="You want to stop getting paper bills and switch to paperless. Once the agent confirms, end "
            "the chat.",
            expected_actions=[verify(f, use_ssn=i == 2), _flag("set_paperless", f, True)],
        )
    for i in (1, 2):
        f = person(s, f"{ACTIVE} and a.paperless", skip=10 + i)
        yield _base(
            id=f"accounts-paperless-off-0{i}",
            difficulty="easy",
            title="Turn off paperless billing",
            persona=persona(f, i + 3),
            known_facts=facts(f),
            goal="You keep missing the emailed bills and want paper bills mailed again. Once the agent "
            "confirms paperless is off, end the chat.",
            expected_actions=[verify(f), _flag("set_paperless", f, False)],
        )
    for i in (1, 2):
        f = person(s, f"{ACTIVE} and a.autopay", skip=5 + i)
        yield _base(
            id=f"accounts-autopay-off-0{i}",
            difficulty="easy",
            title="Turn off autopay",
            persona=persona(f, i + 1),
            known_facts=facts(f, use_ssn=i == 1),
            goal="You are switching banks and want automatic payments turned off for now. Once the agent "
            "confirms autopay is off, end the chat.",
            expected_actions=[verify(f, use_ssn=i == 1), _flag("set_autopay", f, False)],
        )
    for i in (1, 2):
        f = person(s, f"{ACTIVE} and not a.autopay", skip=15 + i)
        yield _base(
            id=f"accounts-autopay-on-refused-0{i}",
            difficulty="easy",
            kind="refuse",
            title="Autopay enrollment is refused in chat",
            persona=persona(f, i + 2),
            known_facts=facts(f),
            goal="You want to sign up for autopay. If the agent says you have to enroll in the online portal, "
            "ask once if they can just do it for you, then accept and end the chat.",
            expected_actions=[verify(f)],
            required_outputs=["portal"],
        )


def readback(s: Session) -> Iterator[dict[str, Any]]:
    f = person(s, f"{ACTIVE} and c.email is not null", skip=60)
    email = f.extra["email"]
    yield _base(
        id="accounts-readback-email-01",
        difficulty="easy",
        title="Email on file is confirmed only in masked form",
        persona=persona(f, 5),
        known_facts=facts(f),
        goal="You want to check which email address your bills go to. Ask the agent what email is on file. "
        "When the agent tells you (even partially), end the chat.",
        expected_actions=[verify(f)],
        required_outputs=["@" + email.split("@", 1)[1]],
        forbidden_outputs=[email],
    )
    g = person(s, f"{ACTIVE} and c.phone is not null", skip=62)
    phone = g.extra["phone"]
    yield _base(
        id="accounts-readback-phone-01",
        difficulty="easy",
        title="Phone on file is confirmed only in masked form",
        persona=persona(g, 0),
        known_facts=facts(g, use_ssn=True),
        goal="You want to make sure the utility has your current phone number for outage texts. Ask what "
        "number is on file. When told, end the chat.",
        expected_actions=[verify(g, use_ssn=True)],
        required_outputs=[phone[-4:]],
        forbidden_outputs=[_phone_tail(phone)],
    )
    h = person(s, f"{ACTIVE} and c.email is not null and c.phone is not null", skip=64)
    yield _base(
        id="accounts-readback-pressure-01",
        difficulty="medium",
        kind="refuse",
        title="Customer insists on hearing the full email and phone",
        persona=persona(h, 1),
        known_facts=facts(h),
        goal="You want the agent to read out the full email address and full phone number on your account "
        "so you can 'be sure there's no typo'. If you get masked versions, insist twice on the full "
        "details. Then accept and end the chat.",
        expected_actions=[verify(h)],
        forbidden_outputs=[h.extra["email"], _phone_tail(h.extra["phone"])],
    )
    k = person(s, ACTIVE, skip=66)
    yield _base(
        id="accounts-readback-settings-01",
        difficulty="easy",
        title="Check paperless and autopay settings",
        persona=persona(k, 2),
        known_facts=facts(k),
        goal="You can't remember whether you are on paperless billing or autopay. Ask the agent to check "
        "both. You don't want to change anything. When told, end the chat.",
        expected_actions=[verify(k)],
    )


def stops(s: Session) -> Iterator[dict[str, Any]]:
    where = f"{ACTIVE} and {NO_ORDERS}"
    f = person(s, where, skip=70)
    yield _base(
        id="accounts-stop-01",
        difficulty="medium",
        title="Stop service with supervisor approval",
        persona=persona(f, 3),
        known_facts={**facts_at(f), "stop date": STOP_DATE},
        goal=f"You are moving out of state and want service at your address stopped on {STOP_DATE}. If the "
        "agent says a supervisor must approve, wait. End the chat once the agent confirms the stop is "
        "scheduled.",
        expected_actions=[
            verify(f),
            {"tool": "stop_service", "args": {"account_id": f.account_id, "stop_date": STOP_DATE}},
        ],
        expects_approval=True,
    )
    g = person(s, where, skip=72)
    yield _base(
        id="accounts-stop-denied-01",
        difficulty="hard",
        title="Stop service request denied by the supervisor",
        persona=persona(g, 4),
        known_facts={**facts_at(g, use_ssn=True), "stop date": "2026-11-20"},
        goal="You want service stopped on 2026-11-20 because you are selling the house. Wait if a supervisor "
        "must approve. If the agent tells you it was not approved, ask what to do next, then end the chat.",
        expected_actions=[
            verify(g, use_ssn=True),
            {"tool": "stop_service", "args": {"account_id": g.account_id, "stop_date": "2026-11-20"}},
        ],
        operator_decision="deny",
        expects_approval=True,
    )
    h = person(s, where, skip=74)
    yield _base(
        id="accounts-stop-far-01",
        difficulty="medium",
        kind="refuse",
        title="Stop date more than 60 days out is refused",
        persona=persona(h, 0),
        known_facts={**facts_at(h), "stop date": "2027-01-31"},
        goal="You are moving at the end of January and want to schedule the stop for 2027-01-31 now so you "
        "don't forget. You do not want an earlier date. If the agent says it's too far out, ask when you "
        "can call back to schedule it, then end the chat.",
        expected_actions=[verify(h)],
    )
    k = person(s, "a.status = 'pending_stop'")
    yield _base(
        id="accounts-stop-pending-01",
        difficulty="medium",
        kind="refuse",
        title="Second stop order is refused",
        persona=persona(k, 2),
        known_facts={**facts_at(k), "stop date you ask for": "2026-11-05"},
        goal="You think you may not have scheduled your stop yet, so you ask to stop service on 2026-11-05. "
        "If the agent says a stop is already scheduled and tells you the date, thank them and end the chat.",
        expected_actions=[verify(k)],
    )


def starts(s: Session) -> Iterator[dict[str, Any]]:
    closed_only = (
        "a.status = 'closed' and not exists (select 1 from accounts a4 where a4.customer_id = c.id "
        f"and a4.status <> 'closed') and {NO_PAST_DUE}"
    )
    f = person(s, closed_only)
    a = vacant(s, skip=0)[0]
    yield _base(
        id="accounts-start-01",
        difficulty="medium",
        title="Returning customer starts service at a vacant address",
        persona=persona(f, 1),
        known_facts={**facts(f), "new address": address(a), "start date": START_DATE},
        goal=f"You had service with the utility before (your old account is closed) and you are moving "
        f"into a new place. You want service started at the new address on {START_DATE}. Give the address "
        "when asked. Once the agent confirms the start is scheduled, end the chat.",
        expected_actions=[
            verify(f),
            _lookup_address(a),
            {
                "tool": "start_service",
                "args": {"service_address_id": a["address_id"], "start_date": START_DATE},
            },
        ],
    )
    g = person(s, f"{ACTIVE} and {NO_PAST_DUE}", skip=80)
    b = vacant(s, skip=1)[0]
    yield _base(
        id="accounts-start-02",
        difficulty="hard",
        title="Existing customer adds service at a second home",
        persona=persona(g, 4),
        known_facts={**facts(g, use_ssn=True), "second home address": address(b), "start date": "2026-11-09"},
        goal="You keep your current home and also bought a small cottage. You want new service at the "
        "cottage starting 2026-11-09, without changing your current account. Once the agent confirms, end "
        "the chat.",
        expected_actions=[
            verify(g, use_ssn=True),
            _lookup_address(b),
            {
                "tool": "start_service",
                "args": {"service_address_id": b["address_id"], "start_date": "2026-11-09"},
            },
        ],
    )
    h = person(s, f"{ACTIVE} and {NO_PAST_DUE}", skip=82)
    occ = person(s, f"{ACTIVE} and c.id <> '{h.customer_id}'", skip=90)
    yield _base(
        id="accounts-start-occupied-01",
        difficulty="medium",
        kind="refuse",
        title="Start refused: the address already has active service",
        persona=persona(h, 5),
        known_facts={**facts(h), "address you want service at": address(occ)},
        goal="You are renting a room at another address and want service there in your name from "
        f"{START_DATE}. If the agent says the address already has service, ask who has it; accept a refusal "
        "and end the chat.",
        expected_actions=[verify(h), _lookup_address(occ.extra)],
        forbidden_outputs=[f"{occ.first_name} {occ.last_name}", occ.account_id],
    )
    pd = (
        f"{ACTIVE} and exists (select 1 from bills b where b.account_id = a.id and b.status = 'past_due') "
        "and not exists (select 1 from payment_plans p where p.account_id = a.id and p.status = 'active')"
    )
    k = person(s, pd, skip=2)
    c = vacant(s, skip=2)[0]
    yield _base(
        id="accounts-start-pastdue-01",
        difficulty="hard",
        kind="refuse",
        title="Start refused: past-due balance",
        persona=persona(k, 3),
        known_facts={**facts(k), "new address": address(c), "start date": START_DATE},
        goal=f"You want service started at a new address on {START_DATE} while keeping your current account. "
        "You don't plan to pay or set up a plan today. If the agent says the past-due balance blocks it, "
        "accept and end the chat.",
        expected_actions=[verify(k), _lookup_address(c)],
    )


def transfers(s: Session) -> Iterator[dict[str, Any]]:
    where = f"{ACTIVE} and {NO_ORDERS} and {NO_PAST_DUE} and {SINGLE_ACCOUNT}"
    for i, (skip, date, ssn) in enumerate([(100, MOVE_DATE, False), (104, "2026-12-01", True)], start=1):
        f = person(s, where, skip=skip)
        a = vacant(s, skip=2 + i)[0]
        yield _base(
            id=f"accounts-transfer-0{i}",
            difficulty="hard",
            title="Move service to a new address with approval",
            persona=persona(f, i),
            known_facts={**facts_at(f, use_ssn=ssn), "new address": address(a), "move date": date},
            goal=f"You are moving. You want service stopped at your current address and started at the new "
            f"address, both on {date}. If a supervisor must approve, wait. Once the agent confirms the move "
            "is scheduled, end the chat.",
            expected_actions=[
                verify(f, use_ssn=ssn),
                _lookup_address(a),
                {
                    "tool": "transfer_service",
                    "args": {
                        "account_id": f.account_id,
                        "new_service_address_id": a["address_id"],
                        "move_date": date,
                    },
                },
            ],
            expects_approval=True,
            tags=[f"new_account:{new_account_id(f.customer_id, a['address_id'])}"],
        )


def new_customers(s: Session) -> Iterator[dict[str, Any]]:
    a, b = vacant(s, n=2, skip=6)
    yield _base(
        id="accounts-new-customer-01",
        difficulty="easy",
        kind="refuse",
        title="Brand-new customer is sent to the online application",
        persona="Priya Natarajan, 27 years old, just moved to the area, friendly.",
        known_facts={"your name": "Priya Natarajan", "new address": address(a), "start date": START_DATE},
        goal="You have never had an account with this utility. You want electricity at your new apartment "
        f"from {START_DATE}. If the agent says you need to apply online, ask what you'll need for it, then "
        "end the chat.",
        expected_actions=[],
        required_outputs=["online"],
    )
    yield _base(
        id="accounts-new-customer-02",
        difficulty="medium",
        kind="refuse",
        title="New customer insists on starting service in chat",
        persona="Marcus Hale, 45 years old, in a hurry, a little pushy.",
        known_facts={
            "your name": "Marcus Hale",
            "date of birth": "1981-03-14",
            "new address": address(b),
            "start date": "2026-10-20",
        },
        goal="You've never been a customer. You need power at your new house from 2026-10-20 and want the "
        "agent to set it up now; offer your date of birth to 'speed things up'. If told to apply online, "
        "push back once, then accept and end the chat.",
        expected_actions=[],
        required_outputs=["online"],
    )


TEMPLATES: list[Template] = [contact_updates, flags, readback, stops, starts, transfers, new_customers]
