"""SQLAlchemy models for the simulated utility, Northbrook Power & Light (fictional).

Identifiers the model sees (customer, account, bill, plan, order...) are short prefixed strings.
Rows created during a conversation get ids from `griddesk.runtime.IdGenerator`, which is
deterministic inside an eval trial so that cached LLM requests replay byte for byte.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(10, 2)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON}


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(20))
    date_of_birth: Mapped[date] = mapped_column(Date)
    ssn_last4: Mapped[str] = mapped_column(String(4))
    medical_necessity: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_contact: Mapped[str] = mapped_column(String(10), default="email")  # email | sms | mail
    mailing_address: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    accounts: Mapped[list[Account]] = relationship(back_populates="customer")


class ServiceAddress(Base):
    __tablename__ = "service_addresses"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    street: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(2))
    zip: Mapped[str] = mapped_column(String(10))
    feeder: Mapped[str] = mapped_column(String(10), index=True)  # distribution circuit, used for outages


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # the customer-facing account number
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    service_address_id: Mapped[str] = mapped_column(ForeignKey("service_addresses.id"), index=True)
    # active | pending_start | pending_stop | closed | disconnect_scheduled
    status: Mapped[str] = mapped_column(String(24))
    rate_plan: Mapped[str] = mapped_column(String(32))  # residential_standard | time_of_use | income_assist
    balance: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    autopay: Mapped[bool] = mapped_column(Boolean, default=False)
    paperless: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_on: Mapped[date] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date)

    customer: Mapped[Customer] = relationship(back_populates="accounts")
    service_address: Mapped[ServiceAddress] = relationship()


class Meter(Base):
    __tablename__ = "meters"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    service_address_id: Mapped[str] = mapped_column(ForeignKey("service_addresses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # smart | analog
    installed_on: Mapped[date] = mapped_column(Date)


class MeterReading(Base):
    __tablename__ = "meter_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meter_id: Mapped[str] = mapped_column(ForeignKey("meters.id"), index=True)
    read_on: Mapped[date] = mapped_column(Date)
    kwh: Mapped[int] = mapped_column(Integer)  # usage for the period ending on read_on
    reading_type: Mapped[str] = mapped_column(String(10))  # actual | estimated


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    issued_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    kwh: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Money)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12))  # paid | open | past_due | disputed

    line_items: Mapped[list[BillLineItem]] = relationship(back_populates="bill", order_by="BillLineItem.id")


class BillLineItem(Base):
    __tablename__ = "bill_line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bill_id: Mapped[str] = mapped_column(ForeignKey("bills.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # energy | delivery | customer_charge | tax | late_fee
    description: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(Money)

    bill: Mapped[Bill] = relationship(back_populates="line_items")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    paid_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(12))  # card | bank | autopay | cash
    status: Mapped[str] = mapped_column(String(10))  # posted | returned


class PaymentPlan(Base):
    __tablename__ = "payment_plans"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    total_amount: Mapped[Decimal] = mapped_column(Money)
    installments: Mapped[int] = mapped_column(Integer)
    installment_amount: Mapped[Decimal] = mapped_column(Money)
    start_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12))  # active | completed | defaulted | cancelled
    created_on: Mapped[date] = mapped_column(Date)
    defaulted_on: Mapped[date | None] = mapped_column(Date)


class Credit(Base):
    __tablename__ = "credits"
    __table_args__ = (
        # Database backstops for P-12 (one credit per outage) and single use of an approval.
        Index(
            "uq_credit_outage",
            "account_id",
            "outage_id",
            unique=True,
            postgresql_where=text("outage_id IS NOT NULL"),
        ),
        Index(
            "uq_credit_approval",
            "approval_request_id",
            unique=True,
            postgresql_where=text("approval_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    # late_fee_waiver | outage_compensation | billing_error | goodwill
    reason_code: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(String(240))
    applied_on: Mapped[date] = mapped_column(Date)
    approval_request_id: Mapped[str | None] = mapped_column(String(16))
    bill_id: Mapped[str | None] = mapped_column(String(16))  # the bill a waiver or correction applies to
    outage_id: Mapped[str | None] = mapped_column(String(16))  # the outage being compensated (P-12)
    conversation_id: Mapped[str | None] = mapped_column(String(64), index=True)  # for the P-10 total


class Outage(Base):
    __tablename__ = "outages"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    feeder: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(12))  # active | restored
    cause: Mapped[str] = mapped_column(String(60))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estimated_restoration: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    customers_affected: Mapped[int] = mapped_column(Integer)


class OutageReport(Base):
    __tablename__ = "outage_reports"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    service_address_id: Mapped[str] = mapped_column(ForeignKey("service_addresses.id"))
    outage_id: Mapped[str | None] = mapped_column(ForeignKey("outages.id"))
    hazard: Mapped[bool] = mapped_column(Boolean, default=False)  # downed line, sparking, fire
    description: Mapped[str] = mapped_column(Text)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServiceOrder(Base):
    __tablename__ = "service_orders"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    # start | stop | transfer | reconnect | disconnect | meter_check
    kind: Mapped[str] = mapped_column(String(16))
    requested_on: Mapped[date] = mapped_column(Date)  # the date the work should happen
    status: Mapped[str] = mapped_column(String(12))  # scheduled | completed | cancelled
    new_service_address_id: Mapped[str | None] = mapped_column(ForeignKey("service_addresses.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdentityVerification(Base):
    __tablename__ = "identity_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("customers.id"))
    account_id: Mapped[str | None] = mapped_column(String(12))
    success: Mapped[bool] = mapped_column(Boolean)
    method: Mapped[str] = mapped_column(String(24))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    action: Mapped[str] = mapped_column(String(40))  # the tool name
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    rule_id: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(12))  # pending | approved | denied | executed
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(40))


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str | None] = mapped_column(String(12))
    # billing_dispute | hazard | identity_lockout | medical | complaint | other
    category: Mapped[str] = mapped_column(String(24))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="open")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_conv_ts", "conversation_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(40))  # agent:<name> | operator:<id> | system
    action: Mapped[str] = mapped_column(String(40))
    account_id: Mapped[str | None] = mapped_column(String(12))
    outcome: Mapped[str] = mapped_column(String(20))  # ok | refused | pending_approval | error
    rule_id: Mapped[str | None] = mapped_column(String(8))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # PII redacted before write


class KbChunk(Base):
    """Knowledge-base passages for policy search (seeded from the handbook)."""

    __tablename__ = "kb_chunks"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    source: Mapped[str] = mapped_column(String(24))  # handbook | bulletin
    title: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)


class Conversation(Base):
    """Index of live chat conversations for the operator console (messages live in the checkpointer)."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="active")  # active | awaiting_approval
    last_message: Mapped[str] = mapped_column(Text, default="")  # PII redacted
    agent: Mapped[str | None] = mapped_column(String(24))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    token_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256 of the customer's secret
