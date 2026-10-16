from __future__ import annotations

import logging
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from nexora.bot.util import send_log
from nexora.moderation.logic import SpamDetector, check_hierarchy, pick_escalation

log = logging.getLogger("nexora.mod")


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._detectors: dict[int, tuple[tuple, SpamDetector]] = {}

    # ---------- shared helpers
    def _guard(self, i: discord.Interaction, target: discord.Member) -> str | None:
        g = i.guild
        return check_hierarchy(actor_id=i.user.id, target_id=target.id, bot_id=g.me.id, guild_owner_id=g.owner_id,
                               actor_top=i.user.top_role.position, target_top=target.top_role.position,
                               bot_top=g.me.top_role.position)

    async def _record(self, i, action, target, reason, color):
        case = await self.bot.store.create_case(i.guild.id, action, target.id, i.user.id, reason)
        cfg = await self.bot.settings_for(i.guild.id)
        await send_log(i.guild, cfg["moderation"]["log_channel_id"], f"Case #{case}: {action}",
                       f"Target: {target} ({target.id})\nModerator: {i.user} ({i.user.id})\nReason: {reason}", color)
        return case

    async def _run(self, i, member, action, reason, color, perm_name, fn):
        await i.response.defer(ephemeral=True)
        if not getattr(i.guild.me.guild_permissions, perm_name):
            return await i.followup.send(f"I need the **{perm_name}** permission.", ephemeral=True)
        if (err := self._guard(i, member)):
            return await i.followup.send(err, ephemeral=True)
        try:
            await fn()
        except discord.Forbidden:
            return await i.followup.send("Discord refused this action (check my role position).", ephemeral=True)
        except discord.HTTPException:
            return await i.followup.send("Discord had a problem; nothing was changed. Try again.", ephemeral=True)
        case = await self._record(i, action, member, reason, color)
        await i.followup.send(f"Done. Case #{case}.", ephemeral=True)

    # ---------- commands
    @app_commands.command(description="Ban a member")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 500] = "No reason",
                  delete_days: app_commands.Range[int, 0, 7] = 0):
        await self._run(i, member, "ban", reason, 0xED4245, "ban_members",
                        lambda: i.guild.ban(member, reason=f"{i.user}: {reason}", delete_message_seconds=delete_days * 86400))

    @app_commands.command(description="Kick a member")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 500] = "No reason"):
        await self._run(i, member, "kick", reason, 0xFAA61A, "kick_members",
                        lambda: i.guild.kick(member, reason=f"{i.user}: {reason}"))

    @app_commands.command(description="Timeout a member (max 28 days)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, i: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320],
                      reason: app_commands.Range[str, 1, 500] = "No reason"):
        await self._run(i, member, "timeout", f"{reason} ({minutes}m)", 0xFAA61A, "moderate_members",
                        lambda: member.timeout(timedelta(minutes=minutes), reason=f"{i.user}: {reason}"))

    @app_commands.command(description="Remove a timeout")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 500] = "No reason"):
        await self._run(i, member, "untimeout", reason, 0x57F287, "moderate_members",
                        lambda: member.timeout(None, reason=f"{i.user}: {reason}"))

    @app_commands.command(description="Warn a member (may trigger automatic escalation)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 500]):
        await i.response.defer(ephemeral=True)
        if (err := self._guard(i, member)):
            return await i.followup.send(err, ephemeral=True)
        case, count = await self.bot.store.add_warning(i.guild.id, member.id, i.user.id, reason)
        cfg = await self.bot.settings_for(i.guild.id)
        m = cfg["moderation"]
        await send_log(i.guild, m["log_channel_id"], f"Case #{case}: warn", f"{member} ({member.id})\nBy: {i.user}\n{reason}", 0xFEE75C)
        if m["dm_on_action"]:
            try:
                await member.send(f"You were warned in **{i.guild.name}**: {reason}")
            except discord.HTTPException:
                pass
        extra = await self._escalate(i.guild, member, count, m)
        await i.followup.send(f"Warned. Case #{case}. Active warnings: {count}.{extra}", ephemeral=True)

    async def _escalate(self, guild, member, count, m) -> str:
        th = pick_escalation(count, m["warn_thresholds"])
        if not th:
            return ""
        err = check_hierarchy(actor_id=guild.me.id, target_id=member.id, bot_id=-1, guild_owner_id=guild.owner_id,
                              actor_top=guild.me.top_role.position, target_top=member.top_role.position,
                              bot_top=guild.me.top_role.position + 1)
        if err:
            return f"\nEscalation `{th['action']}` skipped: {err}"
        try:
            reason = f"auto-escalation at {count} warnings"
            if th["action"] == "timeout":
                await member.timeout(timedelta(minutes=th.get("minutes", 60)), reason=reason)
            elif th["action"] == "kick":
                await guild.kick(member, reason=reason)
            else:
                await guild.ban(member, reason=reason)
        except discord.HTTPException:
            return f"\nEscalation `{th['action']}` failed (permissions)."
        case = await self.bot.store.create_case(guild.id, th["action"], member.id, guild.me.id, reason)
        await send_log(guild, m["log_channel_id"], f"Case #{case}: {th['action']} (auto)", f"{member} ({member.id})\n{reason}", 0xED4245)
        return f"\nAutomatic **{th['action']}** applied."

    @app_commands.command(description="Show a member's active warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, i: discord.Interaction, member: discord.Member):
        rows = await self.bot.store.list_warnings(i.guild.id, member.id)
        if not rows:
            return await i.response.send_message("No active warnings.", ephemeral=True)
        lines = [f"#{r.case_number} <t:{int(r.created_at.timestamp())}:d> by <@{r.moderator_id}>: {r.reason[:100]}" for r in rows]
        await i.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(description="Clear a member's warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarnings(self, i: discord.Interaction, member: discord.Member):
        n = await self.bot.store.clear_warnings(i.guild.id, member.id)
        await self.bot.store.create_case(i.guild.id, "clearwarnings", member.id, i.user.id, f"cleared {n}")
        await i.response.send_message(f"Cleared {n} warning(s).", ephemeral=True)

    # ---------- anti-spam
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if msg.guild is None or msg.author.bot or not isinstance(msg.author, discord.Member):
            return
        cfg = (await self.bot.settings_for(msg.guild.id))["moderation"]
        if not cfg["antispam_enabled"] or msg.channel.id in cfg["ignored_channels"]:
            return
        if {r.id for r in msg.author.roles} & set(cfg["ignored_roles"]) or msg.author.guild_permissions.manage_messages:
            return
        sig = (cfg["max_messages"], cfg["per_seconds"], cfg["duplicate_count"], cfg["mention_limit"])
        cur = self._detectors.get(msg.guild.id)
        if not cur or cur[0] != sig:
            cur = (sig, SpamDetector(*sig))
            self._detectors[msg.guild.id] = cur
        found = cur[1].check((msg.guild.id, msg.author.id), time.monotonic(), msg.content,
                             len(msg.mentions) + len(msg.role_mentions))
        if not found:
            return
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        reason = "auto: " + ", ".join(found)
        case, count = await self.bot.store.add_warning(msg.guild.id, msg.author.id, msg.guild.me.id, reason)
        await send_log(msg.guild, cfg["log_channel_id"], f"Case #{case}: auto-warn", f"{msg.author} ({msg.author.id})\n{reason}", 0xFEE75C)
        await self._escalate(msg.guild, msg.author, count, cfg)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
