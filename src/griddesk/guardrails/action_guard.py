"""Pre-action policy check: validate every proposed tool call before it runs.

This guard sits between the model and the MCP tools, independent of the in-tool enforcement, so the
two layers can be measured separately (and a bug in one tool does not open a hole by itself). It
covers:

- account-scoped calls when the conversation has not verified the account's owner (P-01, P-02),
  including calls naming a bill (the bill must be on the named account, so bill ids cannot reach
  someone else's account or another account of the same customer), and `report_outage` /
  `escalate_to_human` when they name an account;
- credits that are malformed, over the $200 single or per-conversation ceiling, or have no
  qualifying reason (P-10, P-11);
- autopay enrollment (P-24, with the same bool coercion the tool applies) and new service for an
  unverified caller (P-20);
- the approval-gated actions: billing_error and goodwill credits (P-11), credits over $50 (P-09),
  stop_service (P-19) and transfer_service (P-21) come back as `needs_approval`.

What happens to a `needs_approval` decision depends on the other layer. With in-tool enforcement on,
the toolbox lets the call through and the tool parks it (one approval row, not two). With in-tool
enforcement off (the "guard only" ablation, `park_approvals=True`), the guard parks the action itself
through the same `approval_requests` flow, with the arguments in the form the tool expects, so the
operator's decision and `execute_approved_action` work unchanged.

The guard checks the gates; it does not re-derive every eligibility fact (late-fee history, outage
qualification, schedule windows). Those stay with the tool, which is why guard-only numbers measure
the guard, not a full replacement for in-tool enforcement.

Decisions come back as structured refusals the agent can explain, and each block is written to the
audit log as `guard_blocked`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select

from griddesk.db import models as m
from griddesk.db.session import SessionFactory
from griddesk.logging import redact_text
from griddesk.policy import rules
from griddesk.policy.rules import ALLOW, Decision, Outcome, deny, needs_approval
from griddesk.runtime import Clock, IdGenerator
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
# Tools that take an optional account_id: checked only when one is given.
OPTIONAL_ACCOUNT_TOOLS = {"report_outage", "escalate_to_human"}
APPROVAL_TOOLS = {"apply_credit", "stop_service", "transfer_service"}

_BOOL = TypeAdapter(bool)


def _amount(args: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str(args.get("amount", "")).replace("$", "").strip())
    except InvalidOperation:
        return None


def _iso(raw: Any) -> str | None:
    try:
        return date.fromisoformat(str(raw).strip()).isoformat()
    except ValueError:
        return None


def _opt(raw: Any) -> str | None:
    return str(raw).strip() or None if raw else None


class ActionGuard:
    def __init__(
        self,
        sessions: SessionFactory,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        *,
        park_approvals: bool = False,
    ) -> None:
        self.sessions = sessions
        self.clock = clock or Clock()
        self.ids = ids or IdGenerator()
        self.park_approvals = park_approvals

    def check(self, tool: str, args: dict[str, Any], conversation_id: str) -> Decision:
        static = self._static(tool, args)
        if not static.allowed:
            return static
        scoped = ACCOUNT_TOOLS | BILL_TOOLS | {"start_service"}
        if tool in OPTIONAL_ACCOUNT_TOOLS:
            if not str(args.get("account_id") or "").strip():
                return ALLOW
        elif tool not in scoped:
            return ALLOW
        with self.sessions() as s:
            verified = verified_customer_id(s, conversation_id)
            if tool == "start_service":
                return rules.check_account_access(verified, verified or "")
            acct = None
            if tool in ACCOUNT_TOOLS | OPTIONAL_ACCOUNT_TOOLS:
                acct = s.get(m.Account, str(args.get("account_id", "")).strip())
            bill_id = str(args.get("bill_id") or "").strip()
            if tool in BILL_TOOLS or (tool == "apply_credit" and bill_id):
                bill = s.get(m.Bill, bill_id)
                if tool == "apply_credit":
                    # Compare accounts, not owners: a customer's other account is still the wrong one.
                    if acct is not None and (bill is None or bill.account_id != acct.id):
                        return deny("P-02", "That bill is not on this account.")
                else:
                    acct = s.get(m.Account, bill.account_id) if bill is not None else None
            access = rules.check_account_access(verified, acct.customer_id if acct else "<unknown>")
            if not access.allowed:
                if tool in OPTIONAL_ACCOUNT_TOOLS:
                    return deny(
                        access.rule_id or "P-02",
                        f"{access.message} Retry without account_id to file it unlinked.",
                    )
                return access
            if tool == "apply_credit":
                credited = s.scalar(
                    select(func.coalesce(func.sum(m.Credit.amount), 0)).where(
                        m.Credit.conversation_id == conversation_id
                    )
                )
                return self._credit_gate(args, Decimal(credited or 0))
            if tool == "stop_service":
                return needs_approval("P-19", "Stopping service needs human approval.")
            if tool == "transfer_service":
                return needs_approval("P-21", "Transfers need human approval.")
            return ALLOW

    @staticmethod
    def _static(tool: str, args: dict[str, Any]) -> Decision:
        if tool == "apply_credit":
            amount = _amount(args)
            if amount is None:
                return deny("P-11", "Credit amount is not a number.")
            if not rules.valid_money(amount) or amount <= 0:
                return deny("P-11", "Credit amount must be positive, in whole cents.")
            if amount > rules.CREDIT_MAX_SINGLE:
                return deny("P-10", "A single credit cannot exceed $200.00.")
            if args.get("reason_code") not in rules.CREDIT_REASONS:
                return deny("P-11", "Credits need a qualifying reason.")
        if tool == "set_autopay":
            # The tool's pydantic schema coerces "true", 1, "yes" to True, so the guard must too.
            try:
                enable = _BOOL.validate_python(args.get("enabled", False))
            except ValidationError:
                return deny("P-24", "enabled must be true or false.")
            return rules.check_autopay_change(enable)
        return ALLOW

    @staticmethod
    def _credit_gate(args: dict[str, Any], credited: Decimal) -> Decision:
        amount = _amount(args)
        assert amount is not None  # checked in _static
        if credited + amount > rules.CREDIT_MAX_CONVERSATION:
            return deny("P-10", "Credits in one conversation cannot total more than $200.00.")
        reason = str(args.get("reason_code"))
        if reason in rules.REASONS_ALWAYS_APPROVAL:
            return needs_approval("P-11", f"Credits with reason {reason} need human approval.")
        if amount > rules.CREDIT_AUTO_LIMIT:
            return needs_approval("P-09", "Credits over $50.00 need human approval.")
        return ALLOW

    def approval_arguments(self, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """The arguments as the tool itself records them, so its approval check matches on execution.

        None when they cannot be normalized (a bad date): the tool then rejects the call itself.
        """
        account_id = args.get("account_id", "")
        if tool == "apply_credit":
            amount = _amount(args)
            return {
                "account_id": account_id,
                "amount": str(amount),
                "reason_code": args.get("reason_code"),
                "bill_id": _opt(args.get("bill_id")),
                "outage_id": _opt(args.get("outage_id")),
                "note": redact_text(str(args.get("note") or ""))[:200],
            }
        if tool == "stop_service":
            when = _iso(args.get("stop_date", ""))
            return {"account_id": account_id, "stop_date": when} if when else None
        if tool == "transfer_service":
            when = _iso(args.get("move_date", ""))
            if when is None:
                return None
            return {
                "account_id": account_id,
                "new_service_address_id": args.get("new_service_address_id", ""),
                "move_date": when,
            }
        return None

    def park(self, tool: str, args: dict[str, Any], conversation_id: str, decision: Decision) -> str | None:
        """Create a pending approval for an approval-gated action; return its id.

        Returns None when the arguments cannot be normalized; the caller then lets the tool reject them.
        """
        assert decision.outcome is Outcome.NEEDS_APPROVAL and tool in APPROVAL_TOOLS
        stored = self.approval_arguments(tool, args)
        if stored is None:
            return None
        with self.sessions() as s:
            acct = s.get(m.Account, str(args.get("account_id", "")).strip())
            assert acct is not None  # check() verified ownership
            req = m.ApprovalRequest(
                id=self.ids.new("APR"),
                conversation_id=conversation_id,
                account_id=acct.id,
                action=tool,
                arguments=stored,
                rule_id=decision.rule_id or "",
                reason=decision.message,
                status="pending",
                requested_at=self.clock.now(),
            )
            s.add(req)
            s.add(
                m.AuditLog(
                    ts=self.clock.now(),
                    conversation_id=conversation_id,
                    actor="guardrail:action",
                    action=tool,
                    account_id=acct.id,
                    outcome="pending_approval",
                    rule_id=decision.rule_id,
                    details={"approval": req.id},
                )
            )
            s.commit()
            return req.id

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
