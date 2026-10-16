"""Billing tables. UNVERIFIED: written without SQLAlchemy installed; generate/inspect the Alembic
migration and run against real PostgreSQL before relying on it."""
from datetime import datetime

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


class SubscriptionRow(Base):
    __tablename__ = "subscriptions"
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # one row per guild
    plan: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)
    __table_args__ = (CheckConstraint("plan in ('trial','weekly','monthly','yearly')", name="ck_plan_values"),)


class PaymentClaimRow(Base):
    __tablename__ = "payment_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    plan: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    expected_amount_minor: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(32))
    screenshot_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TZ)
    decided_at: Mapped[datetime | None] = mapped_column(TZ)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        CheckConstraint("plan in ('monthly','yearly')", name="ck_claim_plan"),  # no trial/lifetime
        CheckConstraint("status in ('pending','approved','rejected')", name="ck_claim_status"),
        CheckConstraint("expected_amount_minor > 0", name="ck_claim_amount"),
        # A payment reference can back only one open (pending/approved) claim:
        Index("uq_claim_open_reference", "method", "reference", unique=True,
              postgresql_where=text("status in ('pending','approved')")),
    )


class TrialLedgerRow(Base):
    __tablename__ = "trial_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    owner_discord_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TZ)
    __table_args__ = (UniqueConstraint("guild_id", name="uq_trial_guild"),
                      UniqueConstraint("owner_discord_id", name="uq_trial_owner"))


class AuditLogRow(Base):  # append-only: migration should REVOKE UPDATE, DELETE from nexora_app
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    detail: Mapped[dict] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(TZ)


class ReminderLogRow(Base):
    __tablename__ = "reminder_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(TZ)
    days: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("guild_id", "expires_at", "days", name="uq_reminder"),)


class OrderRow(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    plan: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TZ)
    provider_ref: Mapped[str | None] = mapped_column(String(64))
    short_url: Mapped[str | None] = mapped_column(String(255))
    provider_payment_id: Mapped[str | None] = mapped_column(String(64))
    paid_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (
        CheckConstraint("plan in ('weekly','monthly','yearly')", name="ck_order_plan"),  # no lifetime
        CheckConstraint("amount_minor > 0", name="ck_order_amount"),
        CheckConstraint("status in ('created','paid','expired','cancelled','refunded')", name="ck_order_status"),
        # One gateway payment can settle only one order (DB-level duplicate-grant guard):
        Index("uq_order_payment", "provider_payment_id", unique=True, postgresql_where=text("provider_payment_id is not null")),
    )


class ProviderEventRow(Base):  # idempotency: a provider event id can be processed once
    __tablename__ = "provider_events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    ref: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(TZ)
