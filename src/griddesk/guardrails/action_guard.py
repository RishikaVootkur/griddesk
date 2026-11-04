"""Pre-action policy check: validate every proposed tool call before it runs.

This guard sits between the model and the MCP tools, independent of the in-tool enforcement, so the
two layers can be measured separately (and a bug in one tool does not open a hole by itself). It
blocks:

- account-scoped calls when the conversation has not verified the account's owner (P-01, P-02),
  including calls naming a bill, so bill ids cannot be used to reach someone else's account;
- credits that are malformed, over the $200 ceiling, or have no qualifying reason (P-10, P-11);
- autopay enrollment (P-24) and new service for an unverified caller (P-20).

Decisions come back as structured refusals the agent can explain, and each block is written to the
audit log as `guard_blocked`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from griddesk.db import models as m
from griddesk.db.session import SessionFactory
from griddesk.policy import rules
from griddesk.policy.rules import ALLOW, Decision, deny
from griddesk.runtime import Clock
from griddesk.tools.common import verified_customer_id

ACCOUNT_TOOLS = {
    "get_account_summary",
    "list_bills",
    "apply_credit",
    "create_payment_plan",
    "request_meter_check",
    "get_contact_info",
    "update_contact_info",
    "set_paperless",
    "set_autopay",
    "stop_service",
    "transfer_service",
}
BILL_TOOLS = {"explain_bill", "dispute_bill"}


class ActionGuard:
    def __init__(self, sessions: SessionFactory, clock: Clock | None = None) -> None:
        self.sessions = sessions
        self.clock = clock or Clock()

    def check(self, tool: str, args: dict[str, Any], conversation_id: str) -> Decision:
        static = self._static(tool, args)
        if not static.allowed:
            return static
        if tool not in ACCOUNT_TOOLS | BILL_TOOLS | {"start_service"}:
            return ALLOW
        with self.sessions() as s:
            verified = verified_customer_id(s, conversation_id)
            if tool == "start_service":
                return rules.check_account_access(verified, verified or "")
            owner = None
            if tool in BILL_TOOLS or (tool == "apply_credit" and args.get("bill_id")):
                bill = s.get(m.Bill, str(args.get("bill_id", "")).strip())
                if bill is not None:
                    owner = s.scalar(select(m.Account.customer_id).where(m.Account.id == bill.account_id))
            if tool in ACCOUNT_TOOLS:
                acct = s.get(m.Account, str(args.get("account_id", "")).strip())
                acct_owner = acct.customer_id if acct else None
                if owner is not None and acct_owner != owner:
                    return deny("P-02", "That bill does not belong to this account.")
                owner = acct_owner
            return rules.check_account_access(verified, owner or "<unknown>")

    @staticmethod
    def _static(tool: str, args: dict[str, Any]) -> Decision:
        if tool == "apply_credit":
            try:
                amount = Decimal(str(args.get("amount", "")).replace("$", "").strip())
            except InvalidOperation:
                return deny("P-11", "Credit amount is not a number.")
            if not rules.valid_money(amount) or amount <= 0:
                return deny("P-11", "Credit amount must be positive, in whole cents.")
            if amount > rules.CREDIT_MAX_SINGLE:
                return deny("P-10", "A single credit cannot exceed $200.00.")
            if args.get("reason_code") not in rules.CREDIT_REASONS:
                return deny("P-11", "Credits need a qualifying reason.")
        if tool == "set_autopay" and args.get("enabled") is True:
            return rules.check_autopay_change(True)
        return ALLOW

    def record_block(
        self, tool: str, decision: Decision, conversation_id: str, account_id: str | None
    ) -> None:
        with self.sessions() as s:
            s.add(
                m.AuditLog(
                    ts=self.clock.now(),
                    conversation_id=conversation_id,
                    actor="guardrail:action",
                    action=tool,
                    account_id=account_id,
                    outcome="guard_blocked",
                    rule_id=decision.rule_id,
                    details={"message": decision.message},
                )
            )
            s.commit()
