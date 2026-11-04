from datetime import date, timedelta

import pytest
from mcp import Client
from sqlalchemy import select

from griddesk.db import models as m
from griddesk.mcp_servers.accounts import build_accounts_server
from griddesk.mcp_servers.knowledge import build_knowledge_server
from griddesk.mcp_servers.outages import build_outages_server
from griddesk.tools import approvals
from griddesk.tools.knowledge import bm25_scores, rrf, tokens
from tests.tools_helpers import call, make_ctx, one, verify

pytestmark = pytest.mark.db
TODAY = date(2026, 10, 15)

ON_ACTIVE_OUTAGE = """select a.id, a.customer_id, sa.street, sa.zip, sa.id as address_id
    from accounts a join service_addresses sa on sa.id = a.service_address_id
    join outages o on o.feeder = sa.feeder and o.id = 'OUT-2610-01'
    where a.status = 'active' order by a.id limit 1"""
VACANT = """select sa.id, sa.street, sa.zip from service_addresses sa
    where not exists (select 1 from accounts a where a.service_address_id = sa.id) order by sa.id limit 1"""
MOVER = """select a.id, a.customer_id from accounts a where a.status = 'active'
    and not exists (select 1 from bills b where b.account_id = a.id and b.status = 'past_due')
    and not exists (select 1 from service_orders o where o.account_id = a.id) order by a.id limit 1"""


@pytest.fixture
def ctx(sessions):
    return make_ctx(sessions)


async def test_outage_lookup_needs_no_verification_and_reveals_no_names(ctx):
    a = one(ctx, ON_ACTIVE_OUTAGE)
    cust = one(ctx, "select first_name, last_name from customers where id = :c", c=a.customer_id)
    async with Client(build_outages_server(ctx)) as c:
        out = await call(c, "lookup_outage", conversation_id="o1", street=a.street.upper(), zip_code=a.zip)
    assert out["ok"] and out["active_outage"]["outage_id"] == "OUT-2610-01"
    assert "estimate" in out["active_outage"]["note"]
    assert cust.last_name not in str(out) and cust.first_name not in str(out)


async def test_outage_report_links_and_dedupes(ctx):
    a = one(ctx, ON_ACTIVE_OUTAGE)
    verify(ctx, "o2", a.customer_id)
    async with Client(build_outages_server(ctx)) as c:
        first = await call(
            c,
            "report_outage",
            conversation_id="o2",
            street=a.street,
            zip_code=a.zip,
            description="lights out",
            account_id=a.id,
        )
        dup = await call(
            c,
            "report_outage",
            conversation_id="o2",
            street=a.street,
            zip_code=a.zip,
            description="still out",
            account_id=a.id,
        )
        hazard = await call(
            c,
            "report_outage",
            conversation_id="o2",
            street=a.street,
            zip_code=a.zip,
            description="line down and sparking",
            hazard=True,
            account_id=a.id,
        )
    assert first["ok"] and first["linked_outage"]["outage_id"] == "OUT-2610-01"
    assert dup["error"]["rule_id"] == "P-26"
    assert hazard["ok"] and "911" in hazard["message"]  # hazards are always accepted


async def test_estimate_without_etr_says_crews_are_assessing(ctx):
    async with Client(build_outages_server(ctx)) as c:
        out = await call(c, "get_restoration_estimate", conversation_id="o3", outage_id="OUT-2610-02")
    assert out["outage"]["estimated_restoration"] is None and "assessing" in out["outage"]["note"]


async def test_contact_info_is_masked_and_updates_are_validated(ctx):
    a = one(ctx, MOVER)
    verify(ctx, "s1", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        info = await call(c, "get_contact_info", conversation_id="s1", account_id=a.id)
        bad = await call(c, "update_contact_info", conversation_id="s1", account_id=a.id, email="nope")
        good = await call(
            c,
            "update_contact_info",
            conversation_id="s1",
            account_id=a.id,
            email="new.address@example.org",
            phone="617 555 0123",
        )
        auto_on = await call(c, "set_autopay", conversation_id="s1", account_id=a.id, enabled=True)
    assert "***" in info["email"] and info["phone"].startswith("***-")
    assert bad["error"]["rule_id"] == "P-23"
    assert good["ok"] and good["email"] == "n***@example.org"
    assert auto_on["error"]["rule_id"] == "P-24"
    with ctx.sessions() as s:
        assert s.get(m.Customer, a.customer_id).phone == "(617) 555-0123"


async def test_stop_service_waits_for_approval(ctx):
    a = one(ctx, MOVER)
    verify(ctx, "s2", a.customer_id)
    when = (TODAY + timedelta(days=10)).isoformat()
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(c, "stop_service", conversation_id="s2", account_id=a.id, stop_date=when)
        approvals.decide(ctx, out["approval_request_id"], "approve", operator="op")
        done = await call(c, "execute_approved_action", approval_request_id=out["approval_request_id"])
    assert out["error"]["code"] == "needs_approval" and out["error"]["rule_id"] == "P-19"
    assert done["ok"] and done["status"] == "executed"
    with ctx.sessions() as s:
        assert s.get(m.Account, a.id).status == "pending_stop"


async def test_start_service_at_vacant_address(ctx):
    a = one(ctx, MOVER)
    vac = one(ctx, VACANT)
    verify(ctx, "s3", a.customer_id)
    when = (TODAY + timedelta(days=5)).isoformat()
    async with Client(build_accounts_server(ctx)) as c:
        found = await call(c, "lookup_address", conversation_id="s3", street=vac.street, zip_code=vac.zip)
        started = await call(
            c,
            "start_service",
            conversation_id="s3",
            service_address_id=found["service_address_id"],
            start_date=when,
        )
        again = await call(
            c,
            "start_service",
            conversation_id="s3",
            service_address_id=found["service_address_id"],
            start_date=when,
        )
    assert found["has_active_service"] is False
    assert started["ok"] and started["account_status"] == "pending_start"
    assert again["error"]["rule_id"] == "P-20"  # now occupied


async def test_transfer_needs_same_day_and_approval(ctx):
    a = one(ctx, MOVER)
    vac = one(ctx, VACANT)
    verify(ctx, "s4", a.customer_id)
    when = (TODAY + timedelta(days=7)).isoformat()
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(
            c,
            "transfer_service",
            conversation_id="s4",
            account_id=a.id,
            new_service_address_id=vac.id,
            move_date=when,
        )
        approvals.decide(ctx, out["approval_request_id"], "approve", operator="op")
        done = await call(c, "execute_approved_action", approval_request_id=out["approval_request_id"])
    assert out["error"]["rule_id"] == "P-21" and done["ok"]
    with ctx.sessions() as s:
        assert s.get(m.Account, a.id).status == "pending_stop"
        new = s.scalars(select(m.Account).where(m.Account.service_address_id == vac.id)).one()
        assert new.status == "pending_start" and new.customer_id == a.customer_id


async def test_knowledge_search_finds_the_right_rule(ctx):
    async with Client(build_knowledge_server(ctx)) as c:
        out = await call(c, "search_policy", conversation_id="k1", query="how often can a late fee be waived")
        plan = await call(c, "search_policy", conversation_id="k1", query="payment plan minimum balance", k=2)
    assert out["results"][0]["chunk_id"] == "P-08"
    assert "P-15" in [r["chunk_id"] for r in plan["results"]]
    assert "not instructions" in out["note"]


def test_bm25_and_rrf_hand_computed():
    docs = [tokens("late fee waiver"), tokens("payment plan"), tokens("late payment")]
    scores = bm25_scores(tokens("late fee"), docs)
    assert scores[0] > scores[2] > scores[1] == 0.0
    # doc 1 is ranked 1st and 2nd: 1/61 + 1/62; doc 2 only 2nd: 1/62
    fused = rrf([[1, 2], [2, 1]])
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62) and fused[1] == fused[2]
