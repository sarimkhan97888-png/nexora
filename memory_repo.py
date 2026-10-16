"""In-memory repository for tests/dev. Models DB behaviour: one transaction at a time
(like SELECT ... FOR UPDATE serialization) with rollback on exception."""
import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime

from nexora.billing.domain import Claim, ClaimStatus, PayMethod, Subscription


class _Tx:
    def __init__(self, r: "InMemoryRepo"):
        self.r = r

    async def count_recent_created(self, guild_id, since):
        return sum(1 for o in self.r.orders.values() if o.guild_id == guild_id and o.created_at > since)

    async def insert_order(self, o):
        self.r.orders[o.id] = replace(o)

    async def get_order_for_update(self, order_id):
        o = self.r.orders.get(order_id)
        return replace(o) if o else None

    async def get_order_by_payment_for_update(self, payment_id):
        for o in self.r.orders.values():
            if o.provider_payment_id == payment_id:
                return replace(o)
        return None

    async def update_order(self, o):
        self.r.orders[o.id] = replace(o)

    async def payment_used(self, payment_id):
        return any(o.provider_payment_id == payment_id for o in self.r.orders.values())

    async def record_event(self, event_id, kind, ref, at):
        if event_id in self.r.events:
            return False
        self.r.events.add(event_id)
        return True

    async def get_claim_for_update(self, claim_id):
        c = self.r.claims.get(claim_id)
        return replace(c) if c else None

    async def find_open_claim_by_reference(self, method: PayMethod, ref: str):
        for c in self.r.claims.values():
            if c.method == method and c.reference == ref and c.status in (
                    ClaimStatus.PENDING, ClaimStatus.APPROVED):
                return replace(c)
        return None

    async def count_pending(self, guild_id):
        return sum(1 for c in self.r.claims.values()
                   if c.guild_id == guild_id and c.status is ClaimStatus.PENDING)

    async def insert_claim(self, claim):
        if claim.id in self.r.claims:
            raise RuntimeError("duplicate claim id")
        self.r.claims[claim.id] = replace(claim)

    async def update_claim(self, claim):
        self.r.claims[claim.id] = replace(claim)

    async def get_subscription_for_update(self, guild_id):
        return self.r.subs.get(guild_id)

    async def upsert_subscription(self, sub: Subscription):
        self.r.subs[sub.guild_id] = sub

    async def trial_used(self, guild_id, owner_discord_id):
        return any(g == guild_id or o == owner_discord_id for g, o in self.r.trials)

    async def record_trial(self, guild_id, owner_discord_id, at):
        self.r.trials.add((guild_id, owner_discord_id))

    async def add_audit(self, actor, action, guild_id, detail, at: datetime):
        self.r.audit.append({"actor": actor, "action": action, "guild_id": guild_id,
                             "detail": detail, "at": at})

    async def claim_reminder(self, guild_id, expires_at, days):
        key = (guild_id, expires_at, days)
        if key in self.r.reminders:
            return False
        self.r.reminders.add(key)
        return True

    async def subs_expiring_between(self, start, end):
        return [s for s in self.r.subs.values() if s.expires_at and start < s.expires_at <= end]


class InMemoryRepo:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.claims: dict[str, Claim] = {}
        self.subs: dict[int, Subscription] = {}
        self.trials: set[tuple[int, int]] = set()
        self.audit: list[dict] = []
        self.reminders: set = set()
        self.orders: dict = {}
        self.events: set = set()

    @asynccontextmanager
    async def transaction(self):
        async with self._lock:
            snap = copy.deepcopy((self.claims, self.subs, self.trials, self.audit, self.reminders, self.orders, self.events))
            try:
                yield _Tx(self)
            except BaseException:
                self.claims, self.subs, self.trials, self.audit, self.reminders, self.orders, self.events = snap
                raise
