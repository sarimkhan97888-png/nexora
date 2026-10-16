"""PostgreSQL implementation of the billing Repository. UNVERIFIED (no DB/SQLAlchemy available
when written). Row locks: SELECT ... FOR UPDATE; per-guild advisory lock serializes claim limits."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexora.billing.domain import Claim, ClaimStatus, PayMethod, PlanCode, Subscription
from nexora.billing.errors import DuplicateReference
from nexora.billing.orders import Order, OrderStatus
from nexora.db.models import (AuditLogRow, OrderRow, PaymentClaimRow, ProviderEventRow, ReminderLogRow,
                              SubscriptionRow, TrialLedgerRow)


def _claim(r: PaymentClaimRow) -> Claim:
    return Claim(r.id, r.guild_id, r.user_id, PlanCode(r.plan), PayMethod(r.method), r.currency,
                 r.expected_amount_minor, r.reference, r.screenshot_ref, ClaimStatus(r.status),
                 r.created_at, r.decided_at, r.decided_by, r.decision_note)


def _sub(r: SubscriptionRow) -> Subscription:
    return Subscription(r.guild_id, PlanCode(r.plan) if r.plan else None, r.expires_at, r.updated_at)


def _order(r: OrderRow) -> Order:
    return Order(r.id, r.guild_id, r.user_id, PlanCode(r.plan), r.currency, r.amount_minor, OrderStatus(r.status),
                 r.created_at, r.provider_ref, r.short_url, r.provider_payment_id, r.paid_at)


class _Tx:
    def __init__(self, s: AsyncSession):
        self.s = s

    # ---- gateway orders / webhook idempotency
    async def count_recent_created(self, guild_id, since):
        return (await self.s.execute(select(func.count()).select_from(OrderRow).where(
            OrderRow.guild_id == guild_id, OrderRow.created_at > since))).scalar_one()

    async def insert_order(self, o: Order):
        self.s.add(OrderRow(id=o.id, guild_id=o.guild_id, user_id=o.user_id, plan=o.plan.value, currency=o.currency,
                            amount_minor=o.amount_minor, status=o.status.value, created_at=o.created_at))
        await self.s.flush()

    async def get_order_for_update(self, order_id):
        r = (await self.s.execute(select(OrderRow).where(OrderRow.id == order_id).with_for_update())).scalar_one_or_none()
        return _order(r) if r else None

    async def get_order_by_payment_for_update(self, payment_id):
        r = (await self.s.execute(select(OrderRow).where(OrderRow.provider_payment_id == payment_id)
                                  .with_for_update())).scalar_one_or_none()
        return _order(r) if r else None

    async def update_order(self, o: Order):
        r = await self.s.get(OrderRow, o.id)
        r.status, r.provider_ref, r.short_url = o.status.value, o.provider_ref, o.short_url
        r.provider_payment_id, r.paid_at = o.provider_payment_id, o.paid_at

    async def payment_used(self, payment_id):
        return (await self.s.execute(select(OrderRow.id).where(OrderRow.provider_payment_id == payment_id))).first() is not None

    async def record_event(self, event_id, kind, ref, at):
        res = await self.s.execute(pg_insert(ProviderEventRow).values(
            event_id=event_id, kind=kind, ref=(ref or None) and str(ref)[:64], received_at=at).on_conflict_do_nothing())
        return res.rowcount == 1

    async def get_claim_for_update(self, claim_id):
        r = (await self.s.execute(select(PaymentClaimRow).where(PaymentClaimRow.id == claim_id)
                                  .with_for_update())).scalar_one_or_none()
        return _claim(r) if r else None

    async def find_open_claim_by_reference(self, method, ref):
        r = (await self.s.execute(select(PaymentClaimRow).where(
            PaymentClaimRow.method == method.value, PaymentClaimRow.reference == ref,
            PaymentClaimRow.status.in_(["pending", "approved"])))).scalar_one_or_none()
        return _claim(r) if r else None

    async def count_pending(self, guild_id):
        await self.s.execute(text("SELECT pg_advisory_xact_lock(:g)"), {"g": guild_id})
        return (await self.s.execute(select(func.count()).select_from(PaymentClaimRow).where(
            PaymentClaimRow.guild_id == guild_id, PaymentClaimRow.status == "pending"))).scalar_one()

    async def insert_claim(self, c: Claim):
        self.s.add(PaymentClaimRow(
            id=c.id, guild_id=c.guild_id, user_id=c.user_id, plan=c.plan.value, method=c.method.value,
            currency=c.currency, expected_amount_minor=c.expected_amount_minor, reference=c.reference,
            screenshot_ref=c.screenshot_ref, status=c.status.value, created_at=c.created_at,
            decision_note=""))
        try:
            await self.s.flush()
        except IntegrityError:  # partial unique index is the backstop for races
            raise DuplicateReference("this payment reference was already submitted") from None

    async def update_claim(self, c: Claim):
        r = await self.s.get(PaymentClaimRow, c.id)
        r.status, r.decided_at, r.decided_by, r.decision_note = (
            c.status.value, c.decided_at, c.decided_by, c.decision_note)

    async def get_subscription_for_update(self, guild_id):
        r = (await self.s.execute(select(SubscriptionRow).where(SubscriptionRow.guild_id == guild_id)
                                  .with_for_update())).scalar_one_or_none()
        return _sub(r) if r else None

    async def upsert_subscription(self, sub: Subscription):
        v = {"guild_id": sub.guild_id, "plan": sub.plan.value if sub.plan else None,
             "expires_at": sub.expires_at, "updated_at": sub.updated_at}
        stmt = pg_insert(SubscriptionRow).values(**v)
        await self.s.execute(stmt.on_conflict_do_update(
            index_elements=["guild_id"], set_={k: v[k] for k in ("plan", "expires_at", "updated_at")}))

    async def trial_used(self, guild_id, owner_discord_id):
        q = select(TrialLedgerRow.id).where((TrialLedgerRow.guild_id == guild_id) |
                                            (TrialLedgerRow.owner_discord_id == owner_discord_id))
        return (await self.s.execute(q)).first() is not None

    async def record_trial(self, guild_id, owner_discord_id, at):
        self.s.add(TrialLedgerRow(guild_id=guild_id, owner_discord_id=owner_discord_id, created_at=at))
        await self.s.flush()

    async def add_audit(self, actor, action, guild_id, detail, at: datetime):
        self.s.add(AuditLogRow(actor=actor, action=action, guild_id=guild_id, detail=detail, at=at))

    async def claim_reminder(self, guild_id, expires_at, days):
        res = await self.s.execute(pg_insert(ReminderLogRow).values(
            guild_id=guild_id, expires_at=expires_at, days=days).on_conflict_do_nothing())
        return res.rowcount == 1

    async def subs_expiring_between(self, start, end):
        rows = (await self.s.execute(select(SubscriptionRow).where(
            SubscriptionRow.expires_at > start, SubscriptionRow.expires_at <= end))).scalars()
        return [_sub(r) for r in rows]


class SqlBillingRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sm = sessionmaker

    @asynccontextmanager
    async def transaction(self):
        async with self._sm() as session, session.begin():  # commit on success, rollback on error
            yield _Tx(session)
