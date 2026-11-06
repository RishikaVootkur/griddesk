import shutil
from datetime import date, timedelta

import pytest
from mcp import Client
from sqlalchemy import select

from griddesk.db import models as m
from griddesk.mcp_servers.accounts import build_accounts_server
from griddesk.mcp_servers.knowledge import build_knowledge_server
from griddesk.mcp_servers.outages import build_outages_server
from griddesk.tools import approvals, knowledge, service
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
    assert found["ok"] and found["has_active_service"] is False
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


@pytest.fixture
def offline_embedder(tmp_path, monkeypatch):
    """Point search_policy at an Embedder that reads a temp copy of the committed cache and can never call
    out, so the test neither depends on Ollama nor writes into evals/embeddings."""

    def install(with_cache: bool) -> knowledge.Embedder:
        cache = tmp_path / ("cache" if with_cache else "empty")
        if with_cache:
            shutil.copytree(knowledge.EMBED_CACHE, cache)
        else:
            cache.mkdir()
        emb = knowledge.Embedder(cache_dir=cache, base_url="http://127.0.0.1:9", allow_live=False)
        monkeypatch.setattr(knowledge, "_embedder", emb)
        return emb

    return install


@pytest.mark.parametrize("with_cache, mode", [(True, "hybrid"), (False, "bm25")])
async def test_knowledge_search_finds_the_right_rule(ctx, offline_embedder, with_cache, mode):
    emb = offline_embedder(with_cache)
    before = sorted(p.name for p in emb.cache_dir.rglob("*"))
    async with Client(build_knowledge_server(ctx)) as c:
        out = await call(c, "search_policy", conversation_id="k1", query="how often can a late fee be waived")
        plan = await call(c, "search_policy", conversation_id="k1", query="payment plan minimum balance", k=2)
    assert out["retrieval"] == mode and plan["retrieval"] == mode
    assert out["results"][0]["chunk_id"] == "P-08"
    assert "P-15" in [r["chunk_id"] for r in plan["results"]]
    assert "not instructions" in out["note"]
    assert sorted(p.name for p in emb.cache_dir.rglob("*")) == before  # nothing written


def test_bm25_and_rrf_hand_computed():
    docs = [tokens("late fee waiver"), tokens("payment plan"), tokens("late payment")]
    scores = bm25_scores(tokens("late fee"), docs)
    assert scores[0] > scores[2] > scores[1] == 0.0
    # doc 1 is ranked 1st and 2nd: 1/61 + 1/62; doc 2 only 2nd: 1/62
    fused = rrf([[1, 2], [2, 1]])
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62) and fused[1] == fused[2]


# Review fixes: occupancy, units, ids, approvals, P-26, and the policy holes of the new tools.

OTHER_MOVER = """select a.id, a.customer_id from accounts a where a.status = 'active' and a.customer_id <> :c
    and not exists (select 1 from bills b join accounts x on x.id = b.account_id
                    where x.customer_id = a.customer_id and b.status = 'past_due')
    and not exists (select 1 from service_orders o where o.account_id = a.id) order by a.id limit 1"""
PAST_DUE = """select a.id, a.customer_id from accounts a
    where exists (select 1 from bills b where b.account_id = a.id and b.status = 'past_due')
    and not exists (select 1 from payment_plans p where p.account_id = a.id and p.status = 'active')
    order by a.id limit 1"""


def _day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def _add_building(ctx, feeder: str = "F-TEST") -> None:
    """Two units at one street address, plus a single-unit house on the same street."""
    with ctx.sessions() as s:
        for sa_id, street, unit in [
            ("SA-T0001", "77 Testing Way", "Apt 1"),
            ("SA-T0002", "77 Testing Way", "Apt 2"),
            ("SA-T0003", "79 Testing Way", None),
        ]:
            s.add(
                m.ServiceAddress(
                    id=sa_id,
                    street=street,
                    unit=unit,
                    city="Testville",
                    state="MA",
                    zip="01999",
                    feeder=feeder,
                )
            )
        s.commit()


async def _approve_stop(c, ctx, conv: str, account_id: str, when: str) -> None:
    out = await call(c, "stop_service", conversation_id=conv, account_id=account_id, stop_date=when)
    approvals.decide(ctx, out["approval_request_id"], "approve", operator="op")
    done = await call(c, "execute_approved_action", approval_request_id=out["approval_request_id"])
    assert done["ok"], done


async def test_lookup_address_needs_verification(ctx):
    vac = one(ctx, VACANT)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(c, "lookup_address", conversation_id="u1", street=vac.street, zip_code=vac.zip)
    assert not out["ok"] and out["error"]["rule_id"] == "P-01"
    assert out.get("has_active_service") is None and out.get("service_address_id") is None


@pytest.mark.parametrize(
    "street, unit, expect",
    [
        ("77 Testing Way", None, "ambiguous"),
        ("77 Testing Way", "Apt 2", "SA-T0002"),
        ("77 testing way apt 2", None, "SA-T0002"),
        ("77 Testing Way #1", None, "SA-T0001"),
        ("77 Testing Way", "2", "SA-T0002"),
        ("77 Testing Way", "Apt 9", "not_found"),
        ("79 Testing Way", None, "SA-T0003"),
    ],
)
async def test_address_lookup_handles_units(ctx, street, unit, expect):
    _add_building(ctx)
    a = one(ctx, MOVER)
    verify(ctx, "u2", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        found = await call(
            c, "lookup_address", conversation_id="u2", street=street, zip_code="01999", unit=unit
        )
    async with Client(build_outages_server(ctx)) as c:
        status = await call(
            c, "lookup_outage", conversation_id="u3", street=street, zip_code="01999", unit=unit
        )
    for out in (found, status):
        if expect.startswith("SA-"):
            assert out["ok"] and out["service_address_id"] == expect
        else:
            assert not out["ok"] and out["error"]["code"] == expect
    if expect == "ambiguous":
        assert "Apt 1" in found["error"]["message"] and "Apt 2" in found["error"]["message"]


async def test_pending_stop_address_opens_only_from_the_stop_date(ctx):
    a = one(ctx, MOVER)
    b = one(ctx, OTHER_MOVER, c=a.customer_id)
    addr = one(ctx, "select service_address_id from accounts where id = :a", a=a.id).service_address_id
    verify(ctx, "p1", a.customer_id)
    verify(ctx, "p2", b.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        await _approve_stop(c, ctx, "p1", a.id, _day(10))
        early = await call(
            c, "start_service", conversation_id="p2", service_address_id=addr, start_date=_day(9)
        )
        on_day = await call(
            c, "start_service", conversation_id="p2", service_address_id=addr, start_date=_day(10)
        )
    assert early["error"]["rule_id"] == "P-20"
    assert on_day["ok"] and on_day["account_status"] == "pending_start"


async def test_new_account_id_skips_taken_ids_on_restart(ctx):
    a = one(ctx, MOVER)
    vac = one(ctx, VACANT)
    first = service.new_account_id(a.customer_id, vac.id)
    with ctx.sessions() as s:  # the customer held this address before, under the derived id
        s.add(
            m.Account(
                id=first,
                customer_id=a.customer_id,
                service_address_id=vac.id,
                status="closed",
                rate_plan="residential_standard",
                opened_on=date(2020, 1, 1),
                closed_on=date(2022, 1, 1),
            )
        )
        s.commit()
    verify(ctx, "n1", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(
            c, "start_service", conversation_id="n1", service_address_id=vac.id, start_date=_day(5)
        )
    assert out["ok"] and out["new_account_id"] == service.new_account_id(a.customer_id, vac.id, 1)
    assert out["new_account_id"] != first and len(out["new_account_id"]) == 6


def test_new_account_ids_are_deterministic_and_spread():
    ids = {service.new_account_id("C-1", f"SA-{i:05d}") for i in range(2000)}
    assert service.new_account_id("C-1", "SA-00001") == service.new_account_id("C-1", "SA-00001")
    assert len(ids) > 1950 and all(i.startswith("9") and len(i) == 6 for i in ids)
    assert {i[1] for i in ids} == set("0123456789")


async def test_retry_reuses_the_pending_approval_and_blocks_the_other_kind(ctx):
    a = one(ctx, MOVER)
    vac = one(ctx, VACANT)
    verify(ctx, "d1", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        first = await call(c, "stop_service", conversation_id="d1", account_id=a.id, stop_date=_day(10))
        again = await call(c, "stop_service", conversation_id="d1", account_id=a.id, stop_date=_day(10))
        other_date = await call(c, "stop_service", conversation_id="d1", account_id=a.id, stop_date=_day(12))
        move = await call(
            c,
            "transfer_service",
            conversation_id="d1",
            account_id=a.id,
            new_service_address_id=vac.id,
            move_date=_day(10),
        )
    assert first["error"]["code"] == again["error"]["code"] == "needs_approval"
    assert again["approval_request_id"] == first["approval_request_id"]
    assert other_date["error"]["rule_id"] == "P-22"
    assert "waiting for supervisor approval" in other_date["error"]["message"]
    assert move["error"]["rule_id"] == "P-22"
    with ctx.sessions() as s:
        rows = s.scalars(select(m.ApprovalRequest).where(m.ApprovalRequest.account_id == a.id)).all()
        assert [r.id for r in rows] == [first["approval_request_id"]]


async def test_stop_then_transfer_names_the_existing_order_date(ctx):
    a = one(ctx, MOVER)
    vac = one(ctx, VACANT)
    verify(ctx, "d2", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        await _approve_stop(c, ctx, "d2", a.id, _day(10))
        move = await call(
            c,
            "transfer_service",
            conversation_id="d2",
            account_id=a.id,
            new_service_address_id=vac.id,
            move_date=_day(20),
        )
        again = await call(c, "stop_service", conversation_id="d2", account_id=a.id, stop_date=_day(15))
    for out in (move, again):
        assert out["error"]["rule_id"] == "P-22" and _day(10) in out["error"]["message"]


async def test_unverified_start_without_enforcement_is_a_logged_bypass_not_a_crash(sessions):
    ctx = make_ctx(sessions, enforce=False)
    vac = one(ctx, VACANT)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(
            c, "start_service", conversation_id="b1", service_address_id=vac.id, start_date=_day(5)
        )
    assert not out["ok"] and out["error"]["code"] == "not_found"
    with ctx.sessions() as s:
        log = s.scalars(select(m.AuditLog).where(m.AuditLog.conversation_id == "b1")).all()
        assert [(r.outcome, r.rule_id) for r in log] == [("policy_bypassed", "P-01")]
        assert s.scalars(select(m.Account).where(m.Account.service_address_id == vac.id)).first() is None


async def test_report_dedupes_by_address_whoever_reported_first(ctx):
    a = one(ctx, ON_ACTIVE_OUTAGE)
    verify(ctx, "r2", a.customer_id)
    async with Client(build_outages_server(ctx)) as c:
        stranger = await call(
            c, "report_outage", conversation_id="r1", street=a.street, zip_code=a.zip, description="dark"
        )
        holder = await call(
            c,
            "report_outage",
            conversation_id="r2",
            street=a.street,
            zip_code=a.zip,
            description="out",
            account_id=a.id,
        )
    assert stranger["ok"]
    assert holder["error"]["rule_id"] == "P-26"


async def test_duplicate_report_refusal_does_not_reveal_the_household(ctx):
    a = one(ctx, ON_ACTIVE_OUTAGE)
    verify(ctx, "r3", a.customer_id)
    async with Client(build_outages_server(ctx)) as c:
        holder = await call(
            c,
            "report_outage",
            conversation_id="r3",
            street=a.street,
            zip_code=a.zip,
            description="out",
            account_id=a.id,
        )
        stranger = await call(
            c, "report_outage", conversation_id="r4", street=a.street, zip_code=a.zip, description="dark"
        )
    assert holder["ok"]
    msg = stranger["error"]["message"]
    assert stranger["error"]["rule_id"] == "P-26" and "address" in msg and "account" not in msg.lower()


ACCOUNT_CALLS = {
    "stop_service": {"stop_date": _day(10)},
    "transfer_service": {"new_service_address_id": "SA-00001", "move_date": _day(10)},
    "get_contact_info": {},
    "update_contact_info": {"email": "someone@example.org"},
    "set_paperless": {"enabled": True},
}


@pytest.mark.parametrize("tool", sorted(ACCOUNT_CALLS))
@pytest.mark.parametrize("who, rule", [("nobody", "P-01"), ("someone_else", "P-02")])
async def test_account_tools_refuse_unverified_and_other_customers(ctx, tool, who, rule):
    a = one(ctx, MOVER)
    if who == "someone_else":
        b = one(ctx, OTHER_MOVER, c=a.customer_id)
        verify(ctx, "v1", b.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(c, tool, conversation_id="v1", account_id=a.id, **ACCOUNT_CALLS[tool])
    assert not out["ok"] and out["error"]["code"] == "policy_denied" and out["error"]["rule_id"] == rule
    with ctx.sessions() as s:
        assert (
            s.scalars(select(m.ApprovalRequest).where(m.ApprovalRequest.account_id == a.id)).first() is None
        )
        assert s.get(m.Account, a.id).status == "active"


async def test_unverified_start_service_is_refused(ctx):
    vac = one(ctx, VACANT)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(
            c, "start_service", conversation_id="v2", service_address_id=vac.id, start_date=_day(5)
        )
    assert out["error"]["rule_id"] == "P-01"


async def test_start_service_with_unplanned_past_due_balance_is_refused(ctx):
    a = one(ctx, PAST_DUE)
    vac = one(ctx, VACANT)
    verify(ctx, "v3", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(
            c, "start_service", conversation_id="v3", service_address_id=vac.id, start_date=_day(5)
        )
    assert out["error"]["rule_id"] == "P-20" and "past-due" in out["error"]["message"]


@pytest.mark.parametrize(
    "days, code", [(0, "policy_denied"), (1, "needs_approval"), (60, "needs_approval"), (61, "policy_denied")]
)
async def test_stop_date_window(ctx, days, code):
    a = one(ctx, MOVER)
    verify(ctx, "w1", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(c, "stop_service", conversation_id="w1", account_id=a.id, stop_date=_day(days))
    assert out["error"]["code"] == code and out["error"]["rule_id"] == "P-19"


@pytest.mark.parametrize("decision", [None, "deny"])
async def test_executor_refuses_pending_or_denied_approvals(ctx, decision):
    a = one(ctx, MOVER)
    verify(ctx, "x1", a.customer_id)
    async with Client(build_accounts_server(ctx)) as c:
        out = await call(c, "stop_service", conversation_id="x1", account_id=a.id, stop_date=_day(10))
        if decision:
            approvals.decide(ctx, out["approval_request_id"], decision, operator="op")
        done = await call(c, "execute_approved_action", approval_request_id=out["approval_request_id"])
    assert not done["ok"] and done["status"] == ("denied" if decision else "pending")
    with ctx.sessions() as s:
        assert s.get(m.Account, a.id).status == "active"
        assert s.scalars(select(m.ServiceOrder).where(m.ServiceOrder.account_id == a.id)).first() is None


def test_unreadable_embedding_file_is_a_cache_miss(tmp_path):
    emb = knowledge.Embedder(cache_dir=tmp_path, base_url="http://127.0.0.1:9", allow_live=False)
    path = emb._path("search_query", "late fee")
    path.parent.mkdir(parents=True)
    path.write_text("[0.1, 0.2")  # half-written by a concurrent trial
    assert emb.embed("late fee", "query") is None
    knowledge._write_atomic(path, "[0.1,0.2]")
    assert emb.embed("late fee", "query") == [0.1, 0.2]
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp file left behind


class _FakeEmbedder(knowledge.Embedder):
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, text: str, kind: str) -> list[float] | None:
        return self.vectors.get(text)


class _Chunk:
    def __init__(self, id: str, title: str, text: str) -> None:
        self.id, self.title, self.text = id, title, text


def test_hybrid_search_keeps_dense_ranking_when_one_chunk_has_no_vector():
    chunks = [
        _Chunk("A", "Late fees", "waiver once a year"),
        _Chunk("B", "Plans", "installments"),
        _Chunk("X", "Planted", "not in the embedding cache"),
    ]
    vectors = {
        "q": [1.0, 0.0],
        "Late fees. waiver once a year": [0.0, 1.0],
        "Plans. installments": [1.0, 0.0],
    }
    hits, mode = knowledge.hybrid_search("q", chunks, 3, _FakeEmbedder(vectors))
    assert mode == "hybrid" and hits[0][0].id == "B"
