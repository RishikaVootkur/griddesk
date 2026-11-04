import pytest
from mcp import Client

from griddesk.mcp_servers.accounts import build_accounts_server
from tests.tools_helpers import call, make_ctx, one

pytestmark = pytest.mark.db


async def test_verification_lockout_after_three_failures(sessions):
    ctx = make_ctx(sessions)
    c = one(
        ctx,
        "select a.id, cu.last_name, cu.date_of_birth from accounts a join customers cu "
        "on cu.id = a.customer_id order by a.id limit 1",
    )
    async with Client(build_accounts_server(ctx)) as cl:
        fails = [
            await call(
                cl,
                "verify_identity",
                conversation_id="v1",
                account_id=c.id,
                last_name="Wrong",
                date_of_birth=str(c.date_of_birth),
            )
            for _ in range(3)
        ]
        locked = await call(
            cl,
            "verify_identity",
            conversation_id="v1",
            account_id=c.id,
            last_name=c.last_name,
            date_of_birth=str(c.date_of_birth),
        )
        fresh = await call(
            cl,
            "verify_identity",
            conversation_id="v2",
            account_id=c.id,
            last_name=c.last_name,
            date_of_birth=str(c.date_of_birth),
        )
    assert [f["verified"] for f in fails] == [False, False, False]
    assert [f["attempts_remaining"] for f in fails] == [2, 1, 0]
    assert locked["ok"] is False and locked["error"]["rule_id"] == "P-03"
    assert fresh["verified"] is True and fresh["accounts"]


async def test_failed_verification_does_not_say_which_detail_was_wrong(sessions):
    ctx = make_ctx(sessions)
    c = one(
        ctx, "select a.id, cu.last_name from accounts a join customers cu on cu.id = a.customer_id limit 1"
    )
    async with Client(build_accounts_server(ctx)) as cl:
        bad_dob = await call(
            cl,
            "verify_identity",
            conversation_id="v3",
            account_id=c.id,
            last_name=c.last_name,
            date_of_birth="1900-01-01",
        )
        bad_acct = await call(
            cl,
            "verify_identity",
            conversation_id="v3",
            account_id="999999",
            last_name=c.last_name,
            date_of_birth="1900-01-01",
        )
    strip = lambda d: {k: v for k, v in d.items() if k != "attempts_remaining"}  # noqa: E731
    assert strip(bad_dob) == strip(bad_acct)


async def test_escalation_links_only_the_verified_callers_account(sessions):
    ctx = make_ctx(sessions)
    c = one(ctx, "select id from accounts order by id limit 1")
    async with Client(build_accounts_server(ctx)) as cl:
        out = await call(
            cl,
            "escalate_to_human",
            conversation_id="v4",
            category="hazard",
            summary="Downed line on my street",
            account_id=c.id,
        )
        bad = await cl.call_tool(
            "escalate_to_human", {"conversation_id": "v4", "category": "vip", "summary": "x"}
        )
    assert out["ok"] and out["escalation_id"].startswith("ESC-")
    assert bad.is_error
    with ctx.sessions() as s:
        from griddesk.db import models as m

        assert s.get(m.Escalation, out["escalation_id"]).account_id is None
