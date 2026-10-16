"""Non-billing tables. UNVERIFIED against a real database."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nexora.db.models import TZ, Base


class GuildRow(Base):
    __tablename__ = "guilds"
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_id: Mapped[int] = mapped_column(BigInteger)
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(TZ)


class GuildSettingsRow(Base):
    __tablename__ = "guild_settings"
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    previous: Mapped[dict | None] = mapped_column(JSONB)  # for rollback
    updated_at: Mapped[datetime] = mapped_column(TZ)


class ModCaseRow(Base):
    __tablename__ = "mod_cases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    case_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(BigInteger)
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)
    __table_args__ = (UniqueConstraint("guild_id", "case_number", name="uq_case_number"),)


class WarningRow(Base):
    __tablename__ = "warnings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    case_number: Mapped[int] = mapped_column(Integer)
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)
    cleared_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (Index("ix_warn_lookup", "guild_id", "user_id"),)


class TicketRow(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    number: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    opener_id: Mapped[int] = mapped_column(BigInteger)
    category: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TZ)
    closed_at: Mapped[datetime | None] = mapped_column(TZ)
    __table_args__ = (UniqueConstraint("guild_id", "number", name="uq_ticket_number"),
                      Index("ix_ticket_opener", "guild_id", "opener_id"))


class SecurityEventRow(Base):
    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TZ)


class SessionRow(Base):
    __tablename__ = "web_sessions"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(String(100))
    guilds: Mapped[list] = mapped_column(JSONB)  # [{id,name}] the user may manage (token NOT stored)
    csrf: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(TZ)
