"""Data access for guild features and the dashboard. UNVERIFIED against a real database."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexora.api.security import hash_token
from nexora.db.models import AuditLogRow, OrderRow, SubscriptionRow
from nexora.db.models_core import (GuildRow, GuildSettingsRow, ModCaseRow, SecurityEventRow,
                                   SessionRow, TicketRow, WarningRow)
from nexora.settings_schema import merge


class DuplicateTicket(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, sm: async_sessionmaker[AsyncSession]):
        self.sm = sm

    # ---- guilds / settings
    async def upsert_guild(self, guild_id: int, name: str, owner_id: int, present: bool = True):
        async with self.sm() as s, s.begin():
            row = await s.get(GuildRow, guild_id)
            if row:
                row.name, row.owner_id, row.present, row.updated_at = name, owner_id, present, _now()
            else:
                s.add(GuildRow(guild_id=guild_id, name=name, owner_id=owner_id, present=present, updated_at=_now()))

    async def present_guild_ids(self, ids: list[int]) -> set[int]:
        async with self.sm() as s:
            r = await s.execute(select(GuildRow.guild_id).where(GuildRow.guild_id.in_(ids), GuildRow.present))
            return set(r.scalars())

    async def get_settings(self, guild_id: int) -> dict:
        async with self.sm() as s:
            row = await s.get(GuildSettingsRow, guild_id)
            return merge(row.settings if row else None, {})

    async def apply_settings(self, guild_id: int, patch: dict, actor: str) -> dict:
        async with self.sm() as s, s.begin():
            row = (await s.execute(select(GuildSettingsRow).where(GuildSettingsRow.guild_id == guild_id)
                                   .with_for_update())).scalar_one_or_none()
            old = merge(row.settings if row else None, {})
            new = merge(old, patch)
            if row:
                row.previous, row.settings, row.updated_at = old, new, _now()
            else:
                s.add(GuildSettingsRow(guild_id=guild_id, settings=new, previous=old, updated_at=_now()))
            s.add(AuditLogRow(actor=actor, action="settings.changed", guild_id=guild_id,
                              detail={"patch": patch}, at=_now()))
            return new

    async def rollback_settings(self, guild_id: int, actor: str) -> dict | None:
        async with self.sm() as s, s.begin():
            row = (await s.execute(select(GuildSettingsRow).where(GuildSettingsRow.guild_id == guild_id)
                                   .with_for_update())).scalar_one_or_none()
            if not row or row.previous is None:
                return None
            row.settings, row.previous, row.updated_at = row.previous, row.settings, _now()
            s.add(AuditLogRow(actor=actor, action="settings.rolled_back", guild_id=guild_id, detail={}, at=_now()))
            return merge(row.settings, {})

    # ---- moderation
    async def create_case(self, guild_id, action, target_id, moderator_id, reason) -> int:
        async with self.sm() as s, s.begin():
            await s.execute(text("SELECT pg_advisory_xact_lock(:g)"), {"g": guild_id})
            n = (await s.execute(select(func.coalesce(func.max(ModCaseRow.case_number), 0))
                                 .where(ModCaseRow.guild_id == guild_id))).scalar_one() + 1
            s.add(ModCaseRow(guild_id=guild_id, case_number=n, action=action, target_id=target_id,
                             moderator_id=moderator_id, reason=reason[:1000], created_at=_now()))
            return n

    async def add_warning(self, guild_id, user_id, moderator_id, reason) -> tuple[int, int]:
        """Returns (case_number, active_warning_count)."""
        case = await self.create_case(guild_id, "warn", user_id, moderator_id, reason)
        async with self.sm() as s, s.begin():
            s.add(WarningRow(guild_id=guild_id, user_id=user_id, case_number=case, moderator_id=moderator_id,
                             reason=reason[:1000], created_at=_now()))
            await s.flush()
            cnt = (await s.execute(select(func.count()).select_from(WarningRow).where(
                WarningRow.guild_id == guild_id, WarningRow.user_id == user_id,
                WarningRow.cleared_at.is_(None)))).scalar_one()
            return case, cnt

    async def list_warnings(self, guild_id, user_id, limit=10):
        async with self.sm() as s:
            r = await s.execute(select(WarningRow).where(
                WarningRow.guild_id == guild_id, WarningRow.user_id == user_id,
                WarningRow.cleared_at.is_(None)).order_by(WarningRow.id.desc()).limit(limit))
            return list(r.scalars())

    async def clear_warnings(self, guild_id, user_id) -> int:
        async with self.sm() as s, s.begin():
            r = await s.execute(update(WarningRow).where(
                WarningRow.guild_id == guild_id, WarningRow.user_id == user_id,
                WarningRow.cleared_at.is_(None)).values(cleared_at=_now()))
            return r.rowcount

    # ---- tickets
    async def create_ticket(self, guild_id, opener_id, category, reason, max_open) -> TicketRow:
        """Advisory lock per (guild, opener) makes simultaneous clicks safe."""
        async with self.sm() as s, s.begin():
            await s.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"),
                            {"a": guild_id % 2**31, "b": opener_id % 2**31})
            open_n = (await s.execute(select(func.count()).select_from(TicketRow).where(
                TicketRow.guild_id == guild_id, TicketRow.opener_id == opener_id,
                TicketRow.category == category, TicketRow.status.in_(["open", "claimed"])))).scalar_one()
            if open_n >= max_open:
                raise DuplicateTicket()
            n = (await s.execute(select(func.coalesce(func.max(TicketRow.number), 0)).where(
                TicketRow.guild_id == guild_id))).scalar_one() + 1
            t = TicketRow(guild_id=guild_id, number=n, opener_id=opener_id, category=category,
                          status="open", reason=reason[:500], created_at=_now())
            s.add(t)
            await s.flush()
            s.expunge(t)
            return t

    async def get_ticket_by_channel(self, channel_id: int) -> TicketRow | None:
        async with self.sm() as s:
            return (await s.execute(select(TicketRow).where(TicketRow.channel_id == channel_id))).scalar_one_or_none()

    async def update_ticket(self, ticket_id: int, **fields):
        async with self.sm() as s, s.begin():
            await s.execute(update(TicketRow).where(TicketRow.id == ticket_id).values(**fields))

    async def delete_ticket(self, ticket_id: int):  # used if channel creation failed (rollback)
        async with self.sm() as s, s.begin():
            await s.execute(delete(TicketRow).where(TicketRow.id == ticket_id))

    # ---- security / audit
    async def log_security_event(self, guild_id, kind, detail):
        async with self.sm() as s, s.begin():
            s.add(SecurityEventRow(guild_id=guild_id, kind=kind, detail=detail, created_at=_now()))

    async def audit(self, actor, action, guild_id, detail):
        async with self.sm() as s, s.begin():
            s.add(AuditLogRow(actor=actor, action=action, guild_id=guild_id, detail=detail, at=_now()))

    # ---- read models for dashboard
    async def get_subscription(self, guild_id: int):
        from nexora.billing.domain import PlanCode, Subscription
        async with self.sm() as s:
            r = await s.get(SubscriptionRow, guild_id)
            return Subscription(r.guild_id, PlanCode(r.plan) if r.plan else None, r.expires_at, r.updated_at) if r else None

    async def list_orders(self, guild_id: int | None = None, limit: int = 50):
        async with self.sm() as s:
            q = select(OrderRow).order_by(OrderRow.created_at.desc()).limit(limit)
            if guild_id is not None:
                q = q.where(OrderRow.guild_id == guild_id)
            return list((await s.execute(q)).scalars())

    async def list_audit(self, guild_id: int | None = None, limit: int = 50):
        async with self.sm() as s:
            q = select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit)
            if guild_id is not None:
                q = q.where(AuditLogRow.guild_id == guild_id)
            return list((await s.execute(q)).scalars())

    async def overview(self) -> dict:
        async with self.sm() as s:
            guilds = (await s.execute(select(func.count()).select_from(GuildRow).where(GuildRow.present))).scalar_one()
            active = (await s.execute(select(func.count()).select_from(SubscriptionRow).where(
                SubscriptionRow.expires_at > _now()))).scalar_one()
            paid = (await s.execute(select(func.count()).select_from(OrderRow).where(OrderRow.status == "paid"))).scalar_one()
            return {"guilds": guilds, "active_subscriptions": active, "orders_paid": paid}

    # ---- web sessions
    async def create_session(self, token: str, user_id: int, username: str, guilds: list, csrf: str):
        async with self.sm() as s, s.begin():
            await s.execute(delete(SessionRow).where(SessionRow.expires_at < _now()))
            s.add(SessionRow(id_hash=hash_token(token), user_id=user_id, username=username[:100],
                             guilds=guilds, csrf=csrf, expires_at=_now() + timedelta(hours=8)))

    async def get_session(self, token: str) -> SessionRow | None:
        async with self.sm() as s:
            r = await s.get(SessionRow, hash_token(token))
            return r if r and r.expires_at > _now() else None

    async def delete_session(self, token: str):
        async with self.sm() as s, s.begin():
            await s.execute(delete(SessionRow).where(SessionRow.id_hash == hash_token(token)))
