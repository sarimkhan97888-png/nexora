"""Gateway-based billing. Rules:
 * Price/amount/currency come from OUR config, never from the client.
 * Access is granted ONLY from a signature-verified provider webhook whose amount, currency and
   order reference match our own order record. Redirects/screenshots grant nothing.
 * Webhook processing is idempotent (unique provider event id) and runs in one DB transaction."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from nexora.billing.domain import PLAN_DAYS, PURCHASABLE, PlanCode, Subscription, compute_new_expiry
from nexora.billing.errors import BillingError, InvalidInput, PriceNotConfigured, TooManyPending
from nexora.billing.service import BillingConfig
from nexora.core.clock import require_aware


class ProviderError(BillingError):
    code = "provider_unavailable"


class InvalidSignature(Exception):
    pass


class MalformedEvent(Exception):
    pass


class OrderStatus(StrEnum):
    CREATED = "created"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class EventKind(StrEnum):
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    IGNORED = "ignored"


@dataclass
class Order:
    id: str
    guild_id: int
    user_id: int
    plan: PlanCode
    currency: str
    amount_minor: int
    status: OrderStatus
    created_at: datetime
    provider_ref: str | None = None
    short_url: str | None = None
    provider_payment_id: str | None = None
    paid_at: datetime | None = None


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    kind: EventKind
    order_id: str | None = None
    provider_ref: str | None = None
    payment_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None


class PaymentProvider(Protocol):
    async def create_payment_link(self, order: Order, callback_url: str, expire_by: int) -> tuple[str, str]: ...
    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent: ...


class OrderService:
    def __init__(self, repo, config: BillingConfig, provider: PaymentProvider | None, callback_url: str):
        self.repo, self.cfg, self.provider, self.callback_url = repo, config, provider, callback_url

    async def create_checkout(self, *, guild_id: int, user_id: int, plan: PlanCode, currency: str,
                              now: datetime) -> Order:
        now = require_aware(now, "now")
        if self.provider is None:
            raise ProviderError("payments are not configured")
        if plan not in PURCHASABLE:
            raise InvalidInput("choose weekly, monthly or yearly")
        amount = self.cfg.prices.get((plan, currency.upper()))
        if amount is None:
            raise PriceNotConfigured(f"no {plan.value} price in {currency.upper()}")
        async with self.repo.transaction() as tx:
            if await tx.count_recent_created(guild_id, now - timedelta(hours=1)) >= self.cfg.max_pending_per_guild + 2:
                raise TooManyPending("too many recent checkout attempts; try again later")
            order = Order(str(uuid.uuid4()), guild_id, user_id, plan, currency.upper(), amount,
                          OrderStatus.CREATED, now)
            await tx.insert_order(order)
            await tx.add_audit(f"user:{user_id}", "order.created", guild_id,
                               {"order": order.id, "plan": plan.value, "amount_minor": amount,
                                "currency": order.currency}, now)
        try:
            ref, url = await self.provider.create_payment_link(
                order, self.callback_url, int((now + timedelta(minutes=30)).timestamp()))
        except Exception as e:  # noqa: BLE001
            async with self.repo.transaction() as tx:
                o = await tx.get_order_for_update(order.id)
                if o and o.status is OrderStatus.CREATED:
                    await tx.update_order(replace(o, status=OrderStatus.CANCELLED))
            raise ProviderError("could not create the payment link") from e
        async with self.repo.transaction() as tx:
            o = await tx.get_order_for_update(order.id)
            o = replace(o, provider_ref=ref, short_url=url)
            await tx.update_order(o)
            return o

    async def handle_event(self, ev: ProviderEvent, now: datetime) -> str:
        """Returns an outcome string. Raises only on infrastructure errors (so the gateway retries)."""
        now = require_aware(now, "now")
        async with self.repo.transaction() as tx:
            if not await tx.record_event(ev.event_id, ev.kind.value, ev.order_id or ev.payment_id, now):
                return "duplicate"

            async def audit(action, guild_id, **detail):
                await tx.add_audit("gateway", action, guild_id, {"event": ev.event_id, **detail}, now)

            if ev.kind is EventKind.PAID:
                order = await tx.get_order_for_update(ev.order_id) if ev.order_id else None
                if order is None:
                    await audit("payment.unknown_order", None, order=ev.order_id)
                    return "unknown_order"
                if ev.provider_ref and order.provider_ref and ev.provider_ref != order.provider_ref:
                    await audit("payment.rejected", order.guild_id, why="reference", order=order.id)
                    return "rejected_reference"
                if (ev.currency or "").upper() != order.currency:
                    await audit("payment.rejected", order.guild_id, why="currency", order=order.id)
                    return "rejected_currency"
                if ev.amount_minor != order.amount_minor:
                    await audit("payment.rejected", order.guild_id, why="amount", order=order.id,
                                got=ev.amount_minor, expected=order.amount_minor)
                    return "rejected_amount"
                if order.status is OrderStatus.PAID:
                    return "already_paid"
                if order.status is OrderStatus.REFUNDED:
                    return "ignored"
                if ev.payment_id and await tx.payment_used(ev.payment_id):
                    await audit("payment.rejected", order.guild_id, why="payment_reused", order=order.id)
                    return "rejected_duplicate_payment"
                sub = await tx.get_subscription_for_update(order.guild_id)
                new_exp = compute_new_expiry(sub.expires_at if sub else None, now, order.plan)
                await tx.upsert_subscription(Subscription(order.guild_id, order.plan, new_exp, now))
                await tx.update_order(replace(order, status=OrderStatus.PAID, paid_at=now,
                                              provider_payment_id=ev.payment_id))
                await audit("payment.verified", order.guild_id, order=order.id, plan=order.plan.value,
                            new_expiry=new_exp.isoformat())
                return "granted"

            if ev.kind in (EventKind.EXPIRED, EventKind.CANCELLED):
                order = await tx.get_order_for_update(ev.order_id) if ev.order_id else None
                if order and order.status is OrderStatus.CREATED:
                    st = OrderStatus.EXPIRED if ev.kind is EventKind.EXPIRED else OrderStatus.CANCELLED
                    await tx.update_order(replace(order, status=st))
                    return st.value
                return "ignored"

            if ev.kind is EventKind.REFUNDED:
                order = await tx.get_order_by_payment_for_update(ev.payment_id) if ev.payment_id else None
                if order is None or order.status is not OrderStatus.PAID:
                    return "ignored"
                if ev.amount_minor != order.amount_minor:  # partial refund: owner decides manually
                    await audit("payment.partial_refund", order.guild_id, order=order.id)
                    return "partial_refund"
                sub = await tx.get_subscription_for_update(order.guild_id)
                if sub and sub.expires_at:  # undo exactly this order's days
                    exp = max(now, sub.expires_at - timedelta(days=PLAN_DAYS[order.plan]))
                    await tx.upsert_subscription(replace(sub, expires_at=exp, updated_at=now))
                await tx.update_order(replace(order, status=OrderStatus.REFUNDED))
                await audit("payment.refunded", order.guild_id, order=order.id)
                return "refunded"

            if ev.kind is EventKind.DISPUTED:  # no automatic action: owner reviews and may /revoke
                order = await tx.get_order_by_payment_for_update(ev.payment_id) if ev.payment_id else None
                await audit("payment.disputed", order.guild_id if order else None,
                            order=order.id if order else None)
                return "disputed"
            return "ignored"
