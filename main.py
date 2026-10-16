"""Discord bot entrypoint. UNVERIFIED: written without discord.py installed; test on a dev server first."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexora.ai.provider import AIService, OpenAICompatibleProvider, UsageLimiter
from nexora.billing.domain import effective_entitlements
from nexora.billing.service import BillingConfig, BillingService
from nexora.billing.orders import OrderService
from nexora.billing.razorpay import RazorpayProvider
from nexora.core.clock import utcnow
from nexora.core.config import Settings
from nexora.core.logging import setup_logging
from nexora.db.billing_repo import SqlBillingRepository
from nexora.db.store import Store

log = logging.getLogger("nexora.bot")
COGS = ["moderation", "admin", "tickets", "security", "ai", "pay"]


class Nexora(commands.Bot):
    def __init__(self, cfg: Settings):
        intents = discord.Intents.default()
        intents.members = True          # privileged: enable in Developer Portal
        intents.message_content = True  # privileged: needed for anti-spam and AI
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.cfg = cfg
        self._settings_cache: dict[int, tuple[float, dict]] = {}
        self._stop = asyncio.Event()

    async def setup_hook(self):
        engine = create_async_engine(self.cfg.database_url, pool_size=self.cfg.db_pool_size,
                                     max_overflow=self.cfg.db_max_overflow, pool_pre_ping=True, pool_recycle=300)
        self.engine = engine
        sm = async_sessionmaker(engine, expire_on_commit=False)
        self.store = Store(sm)
        repo = SqlBillingRepository(sm)
        bcfg = BillingConfig(self.cfg.prices, self.cfg.grace_days, self.cfg.max_pending_orders, self.cfg.reminder_days)
        self.billing = BillingService(repo, bcfg)
        pay = (RazorpayProvider(self.cfg.razorpay_key_id, self.cfg.razorpay_key_secret, self.cfg.razorpay_webhook_secret)
               if self.cfg.payment_provider == "razorpay" and self.cfg.razorpay_key_id else None)
        self.orders = OrderService(repo, bcfg, pay, f"{self.cfg.public_base_url}/billing/thanks")
        provider = (OpenAICompatibleProvider(self.cfg.ai_base_url, self.cfg.ai_api_key, self.cfg.ai_model)
                    if self.cfg.ai_api_key and self.cfg.ai_base_url else None)
        self.ai = AIService(provider, UsageLimiter())
        Path(self.cfg.transcript_dir).mkdir(parents=True, exist_ok=True)
        for c in COGS:
            await self.load_extension(f"nexora.bot.cogs.{c}")
        self.tree.on_error = self.on_app_error
        await self.tree.sync()
        self.heartbeat.start()

    @tasks.loop(seconds=30)
    async def heartbeat(self):
        Path("/tmp/heartbeat").touch()

    async def close(self):
        self._stop.set()  # graceful: stop Discord, then close the DB pool
        await super().close()
        if hasattr(self, "engine"):
            await self.engine.dispose()

    async def settings_for(self, guild_id: int) -> dict:
        hit = self._settings_cache.get(guild_id)
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        s = await self.store.get_settings(guild_id)
        self._settings_cache[guild_id] = (time.monotonic(), s)
        return s

    def invalidate(self, guild_id: int):
        self._settings_cache.pop(guild_id, None)

    async def entitlements(self, guild_id: int):
        sub = await self.store.get_subscription(guild_id)
        return effective_entitlements(sub, utcnow(), self.cfg.grace_days)

    async def on_app_error(self, i: discord.Interaction, err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            msg = "You don't have permission to use this command."
        elif isinstance(err, app_commands.BotMissingPermissions):
            msg = "I'm missing permissions: " + ", ".join(err.missing_permissions)
        elif isinstance(err, app_commands.CommandOnCooldown):
            msg = f"Slow down, try again in {err.retry_after:.0f}s."
        else:
            log.exception("command error", exc_info=err)
            msg = "Something went wrong. Please try again later."
        try:
            if i.response.is_done():
                await i.followup.send(msg, ephemeral=True)
            else:
                await i.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


def run():
    """Standalone bot (only needed if you split bot and website into two services)."""
    cfg = Settings.from_env()
    setup_logging(cfg.log_level, cfg.sentry_dsn)
    Nexora(cfg).run(cfg.discord_token, log_handler=None)
