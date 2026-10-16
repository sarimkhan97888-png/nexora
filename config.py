from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nexora.billing.domain import PlanCode, parse_price_table


def _async_url(u: str) -> str:
    """Neon/Render give postgres(ql):// URLs with ?sslmode=require. SQLAlchemy+asyncpg needs
    postgresql+asyncpg:// and ?ssl=require (asyncpg rejects 'sslmode')."""
    for p in ("postgres://", "postgresql://"):
        if u.startswith(p):
            u = "postgresql+asyncpg://" + u[len(p):]
            break
    if not u.startswith("postgresql+asyncpg://"):
        return u
    parts = urlsplit(u)
    q = dict(parse_qsl(parts.query))
    mode = q.pop("sslmode", None)
    q.pop("channel_binding", None)
    if mode in ("require", "verify-ca", "verify-full") and "ssl" not in q:
        q["ssl"] = "require"
    return urlunsplit(parts._replace(query=urlencode(q)))


def _ids(v: str) -> frozenset[int]:
    return frozenset(int(x) for x in v.split(",") if x.strip())


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    migration_url: str
    discord_token: str
    discord_client_id: str
    discord_client_secret: str
    oauth_redirect_uri: str
    public_base_url: str
    session_secret: str
    owner_discord_ids: frozenset[int]
    payment_provider: str
    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    prices: dict[tuple[PlanCode, str], int]
    grace_days: int = 2
    reminder_days: tuple[int, ...] = (3, 1)
    max_pending_orders: int = 5
    transcript_dir: str = "/tmp/transcripts"
    db_pool_size: int = 3
    db_max_overflow: int = 2
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    sentry_dsn: str = ""
    log_level: str = "INFO"
    secure_cookies: bool = True
    run_bot: bool = True        # bot runs inside the web process (needed for Render free)
    auto_migrate: bool = True   # free Render has no pre-deploy command
    keepalive: bool = True      # self-ping so the free instance is less likely to sleep

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        g = env.get
        db = _async_url(g("DATABASE_URL", ""))
        base = g("PUBLIC_BASE_URL", "")
        return cls(
            environment=g("ENVIRONMENT", "development"), database_url=db,
            migration_url=_async_url(g("DATABASE_MIGRATION_URL") or db),
            discord_token=g("DISCORD_BOT_TOKEN", ""), discord_client_id=g("DISCORD_CLIENT_ID", ""),
            discord_client_secret=g("DISCORD_CLIENT_SECRET", ""),
            oauth_redirect_uri=g("DISCORD_OAUTH_REDIRECT_URI", ""), public_base_url=base,
            session_secret=g("SESSION_SECRET", ""), owner_discord_ids=_ids(g("OWNER_DISCORD_IDS", "")),
            payment_provider=g("PAYMENT_PROVIDER", "razorpay"),
            razorpay_key_id=g("RAZORPAY_KEY_ID", ""), razorpay_key_secret=g("RAZORPAY_KEY_SECRET", ""),
            razorpay_webhook_secret=g("RAZORPAY_WEBHOOK_SECRET", ""),
            prices=parse_price_table(g("PRICES", "")), grace_days=int(g("GRACE_DAYS", "2")),
            reminder_days=tuple(int(x) for x in g("REMINDER_DAYS", "3,1").split(",") if x),
            max_pending_orders=int(g("MAX_PENDING_ORDERS", "5")),
            transcript_dir=g("TRANSCRIPT_DIR", "/tmp/transcripts"),
            db_pool_size=int(g("DB_POOL_SIZE", "3")), db_max_overflow=int(g("DB_MAX_OVERFLOW", "2")),
            ai_base_url=g("AI_BASE_URL", ""), ai_api_key=g("AI_API_KEY", ""), ai_model=g("AI_MODEL", ""),
            sentry_dsn=g("SENTRY_DSN", ""), log_level=g("LOG_LEVEL", "INFO"),
            secure_cookies=base.startswith("https://"),
            run_bot=_bool(g("RUN_BOT"), True), auto_migrate=_bool(g("AUTO_MIGRATE"), True),
            keepalive=_bool(g("KEEPALIVE"), base.startswith("https://")))
