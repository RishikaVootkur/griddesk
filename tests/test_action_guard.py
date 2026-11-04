import pytest

from griddesk.guardrails.action_guard import ActionGuard
from tests.test_billing_server import WAIVABLE
from tests.tools_helpers import make_ctx, one, verify

pytestmark = pytest.mark.db


def test_guard_requires_the_verified_owner(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    other = one(
        ctx, "select id from accounts where customer_id <> :c order by id limit 1", c=acct.customer_id
    )
    guard = ActionGuard(sessions)
    assert guard.check("get_account_summary", {"account_id": acct.id}, "g1").rule_id == "P-01"
    verify(ctx, "g1", acct.customer_id)
    assert guard.check("get_account_summary", {"account_id": acct.id}, "g1").allowed
    assert guard.check("set_paperless", {"account_id": other.id, "enabled": True}, "g1").rule_id == "P-02"


def test_guard_blocks_bill_ids_from_other_accounts(sessions):
    ctx = make_ctx(sessions)
    acct = one(ctx, WAIVABLE)
    foreign_bill = one(
        ctx,
        "select b.id from bills b join accounts a on a.id = b.account_id "
        "where a.customer_id <> :c order by b.id limit 1",
        c=acct.customer_id,
    )
    verify(ctx, "g2", acct.customer_id)
    guard = ActionGuard(sessions)
    assert guard.check("explain_bill", {"bill_id": foreign_bill.id}, "g2").rule_id == "P-02"
    mixed = {
        "account_id": acct.id,
        "amount": "15.00",
        "reason_code": "late_fee_waiver",
        "bill_id": foreign_bill.id,
    }
    assert guard.check("apply_credit", mixed, "g2").rule_id == "P-02"


@pytest.mark.parametrize(
    ("args", "rule"),
    [
        ({"amount": "250", "reason_code": "goodwill"}, "P-10"),
        ({"amount": "12.345", "reason_code": "goodwill"}, "P-11"),
        ({"amount": "abc", "reason_code": "goodwill"}, "P-11"),
        ({"amount": "20", "reason_code": "because"}, "P-11"),
    ],
)
def test_guard_static_credit_checks(sessions, args, rule):
    assert ActionGuard(sessions).check("apply_credit", {"account_id": "x", **args}, "g3").rule_id == rule


def test_guard_blocks_autopay_enrollment_and_unverified_starts(sessions):
    guard = ActionGuard(sessions)
    assert guard.check("set_autopay", {"account_id": "x", "enabled": True}, "g4").rule_id == "P-24"
    assert (
        guard.check("start_service", {"service_address_id": "SA-1", "start_date": "2026-10-20"}, "g4").rule_id
        == "P-01"
    )
    assert guard.check("lookup_outage", {"street": "1 Main St"}, "g4").allowed
