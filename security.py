from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from nexora.bot.util import role_assignable, send_log
from nexora.security.raid import JoinRateMonitor, advisory_note, plan_response

log = logging.getLogger("nexora.security")


class Security(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._mon: dict[int, tuple[tuple, JoinRateMonitor]] = {}
        self._active_raid: set[int] = set()

    @commands.Cog.listener()
    async def on_member_join(self, m: discord.Member):
        cfg = (await self.bot.settings_for(m.guild.id))["security"]
        if not cfg["enabled"] or cfg["emergency_disable"]:
            return
        sig = (cfg["join_threshold"], cfg["window_seconds"])
        cur = self._mon.get(m.guild.id)
        if not cur or cur[0] != sig:
            cur = (sig, JoinRateMonitor(*sig))
            self._mon[m.guild.id] = cur
        now = datetime.now(UTC).timestamp()
        triggered = cur[1].record(now)
        if triggered:
            self._active_raid.add(m.guild.id)
        age = (datetime.now(UTC) - m.created_at).days
        note = advisory_note(age)
        if note:  # logged for staff only; never used to punish
            await self.bot.store.log_security_event(m.guild.id, "new_account_join", {"user": m.id, "note": note})
        actions = plan_response(cfg, triggered)
        if "alert" in actions:
            await self.bot.store.log_security_event(m.guild.id, "raid_alert", {"threshold": sig})
            await send_log(m.guild, cfg["alert_channel_id"], "Possible raid",
                           f"{sig[0]}+ joins within {sig[1]}s. Quarantine: {'on' if 'quarantine' in actions else 'off'}.", 0xED4245)
        if m.guild.id in self._active_raid and "quarantine" in plan_response(cfg, True):
            role = m.guild.get_role(cfg["quarantine_role_id"])
            if role_assignable(m.guild, role) is None:
                try:
                    await m.add_roles(role, reason="NEXORA raid quarantine (reversible)")
                    await self.bot.store.log_security_event(m.guild.id, "quarantined", {"user": m.id})
                except discord.HTTPException:
                    pass

    security = app_commands.Group(name="security", description="Anti-raid controls", guild_only=True,
                                  default_permissions=discord.Permissions(manage_guild=True))

    @security.command(description="Emergency switch: disable ALL automated security actions")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def emergency(self, i: discord.Interaction, disable_automation: bool):
        await self.bot.store.apply_settings(i.guild.id, {"security": {"emergency_disable": disable_automation}}, f"user:{i.user.id}")
        self.bot.invalidate(i.guild.id)
        await i.response.send_message("Automation DISABLED." if disable_automation else "Automation re-enabled.", ephemeral=True)

    @security.command(description="End raid mode for this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def endraid(self, i: discord.Interaction):
        self._active_raid.discard(i.guild.id)
        await i.response.send_message("Raid mode ended. Use /security release to un-quarantine members.", ephemeral=True)

    @security.command(description="Remove the quarantine role from a member (or everyone with it)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def release(self, i: discord.Interaction, member: discord.Member | None = None):
        await i.response.defer(ephemeral=True)
        cfg = (await self.bot.settings_for(i.guild.id))["security"]
        role = i.guild.get_role(cfg["quarantine_role_id"] or 0)
        if role is None:
            return await i.followup.send("No quarantine role configured.", ephemeral=True)
        targets = [member] if member else list(role.members)
        n = 0
        for t in targets[:200]:
            try:
                await t.remove_roles(role, reason=f"released by {i.user}")
                n += 1
            except discord.HTTPException:
                pass
        await i.followup.send(f"Released {n} member(s).", ephemeral=True)

    @security.command(description="Show security status")
    async def status(self, i: discord.Interaction):
        cfg = (await self.bot.settings_for(i.guild.id))["security"]
        await i.response.send_message(f"```{cfg}```\nRaid mode: {i.guild.id in self._active_raid}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Security(bot))
