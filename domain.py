"""Pure billing rules. Only three durations exist: 7-day trial, 30-day monthly, 365-day yearly.
There is deliberately no lifetime plan."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from nexora.core.clock import require_aware


class PlanCode(StrEnum):
    TRIAL = "trial"   # free 7-day trial (not exposed in the bot any more)
    WEEKLY = "weekly"  # paid 7-day plan
    MONTHLY = "monthly"
    YEARLY = "yearly"


PLAN_DAYS: dict[PlanCode, int] = {PlanCode.TRIAL: 7, PlanCode.WEEKLY: 7, PlanCode.MONTHLY: 30, PlanCode.YEARLY: 365}
PURCHASABLE = (PlanCode.WEEKLY, PlanCode.MONTHLY, PlanCode.YEARLY)


class AccessState(StrEnum):
    FREE = "free"
    ACTIVE = "active"
    GRACE = "grace"
    EXPIRED = "expired"


class PayMethod(StrEnum):
    UPI = "upi"
    PAYPAL = "paypal"
    OTHER = "other"


class ClaimStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Entitlements:
    ticket_panels: int
    ai_requests_per_day: int
    economy: bool
    custom_embeds: int
    transcripts: bool
    advanced_security: bool


# Defaults chosen by the developer; edit HERE (single source of truth). Not confirmed by the owner.
FREE = Entitlements(1, 0, False, 1, False, False)
PAID = Entitlements(10, 200, True, 25, True, True)
PLAN_ENTITLEMENTS = {PlanCode.TRIAL: PAID, PlanCode.WEEKLY: PAID, PlanCode.MONTHLY: PAID, PlanCode.YEARLY: PAID}


@dataclass(frozen=True)
class Subscription:
    guild_id: int
    plan: PlanCode | None
    expires_at: datetime | None
    updated_at: datetime


@dataclass
class Claim:
    id: str
    guild_id: int
    user_id: int
    plan: PlanCode
    method: PayMethod
    currency: str
    expected_amount_minor: int
    reference: str  # normalized UTR / PayPal transaction id
    screenshot_ref: str | None
    status: ClaimStatus
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: int | None = None
    decision_note: str = ""


def parse_price_table(raw: str) -> dict[tuple[PlanCode, str], int]:
    """'monthly:INR:19900,yearly:INR:199900' -> {(MONTHLY,'INR'): 19900, ...} (minor units)."""
    out: dict[tuple[PlanCode, str], int] = {}
    for item in (p.strip() for p in raw.split(",")):
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 3:
            raise ValueError(f"bad price entry {item!r}; expected plan:CUR:minor_units")
        plan_s, cur, amt = parts
        try:
            plan = PlanCode(plan_s.strip().lower())
        except ValueError:
            raise ValueError(f"unknown plan {plan_s!r}; allowed: weekly, monthly, yearly") from None
        if plan not in PURCHASABLE:
            raise ValueError("trial is free and cannot have a price")
        cur = cur.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            raise ValueError(f"bad currency {cur!r}")
        if not amt.strip().isdigit() or int(amt) <= 0:
            raise ValueError(f"bad amount {amt!r}")
        if (plan, cur) in out:
            raise ValueError(f"duplicate price for {plan.value}/{cur}")
        out[(plan, cur)] = int(amt)
    if not out:
        raise ValueError("no prices configured")
    return out


_REF_RE = re.compile(r"[A-Z0-9\-]{8,32}")


def normalize_reference(raw: str) -> str:
    ref = re.sub(r"[\s]", "", raw or "").upper()
    if not _REF_RE.fullmatch(ref):
        raise ValueError("reference must be 8-32 letters/digits (UTR / transaction ID)")
    return ref


def compute_new_expiry(current: datetime | None, now: datetime, plan: PlanCode) -> datetime:
    """Early renewal stacks onto the remaining time; lapsed subscriptions restart from 'now'."""
    now = require_aware(now, "now")
    base = now
    if current is not None:
        current = require_aware(current, "current expiry")
        if current > now:
            base = current
    return (base + timedelta(days=PLAN_DAYS[plan])).astimezone(UTC)


def access_state(sub: Subscription | None, now: datetime, grace_days: int) -> AccessState:
    now = require_aware(now, "now")
    if sub is None or sub.expires_at is None or sub.plan is None:
        return AccessState.FREE
    if now < sub.expires_at:
        return AccessState.ACTIVE
    if now < sub.expires_at + timedelta(days=grace_days):
        return AccessState.GRACE
    return AccessState.EXPIRED


def effective_entitlements(sub: Subscription | None, now: datetime, grace_days: int) -> Entitlements:
    """Paid features apply while ACTIVE or in GRACE; otherwise FREE. Data is never deleted."""
    state = access_state(sub, now, grace_days)
    if state in (AccessState.ACTIVE, AccessState.GRACE) and sub and sub.plan:
        return PLAN_ENTITLEMENTS[sub.plan]
    return FREE
