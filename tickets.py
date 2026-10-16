from __future__ import annotations

import io
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from nexora.bot.util import send_log
from nexora.core.clock import utcnow
from nexora.db.store import DuplicateTicket
from nexora.tickets.logic import InvalidTransition, TicketStatus, can_act, is_staff, next_status

log = logging.getLogger("nexora.tickets")
CAT = "support"


async def _staff(bot, i: discord.Interaction) -> bool:
    cfg = (await bot.settings_for(i.guild.id))["tickets"]
    return is_staff({r.id for r in i.user.roles}, set(cfg["support_role_ids"]), i.user.guild_permissions.manage_channels)


class TicketModal(discord.ui.Modal, title="Open a ticket"):
    reason = discord.ui.TextInput(label="How can we help?", required=False, max_length=500,
                                  style=discord.TextStyle.paragraph)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True)  # channel creation can exceed 3s
        bot, g = self.bot, i.guild
        cfg = (await bot.settings_for(g.id))["tickets"]
        ent = await bot.entitlements(g.id)
        if ent.ticket_panels < 1:
            return await i.followup.send("Tickets are not available on this plan.", ephemeral=True)
        try:
            t = await bot.store.create_ticket(g.id, i.user.id, CAT, str(self.reason), cfg["max_open_per_user"])
        except DuplicateTicket:
            return await i.followup.send("You already have an open ticket.", ephemeral=True)
        ow = {g.default_role: discord.PermissionOverwrite(view_channel=False),
              g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                               read_message_history=True, attach_files=True, embed_links=True),
              i.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                  attach_files=True)}
        for rid in cfg["support_role_ids"]:
            if (r := g.get_role(rid)):
                ow[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        try:
            cat = g.get_channel(cfg["category_id"]) if cfg["category_id"] else None
            ch = await g.create_text_channel(f"ticket-{t.number:04d}", category=cat, overwrites=ow,
                                             reason=f"ticket by {i.user}")
        except discord.HTTPException:
            await bot.store.delete_ticket(t.id)  # roll back the reservation
            return await i.followup.send("I couldn't create the channel (check my permissions).", ephemeral=True)
        await bot.store.update_ticket(t.id, channel_id=ch.id)
        await ch.send(f"{i.user.mention} ticket opened. Staff will be with you soon.\n**Reason:** {self.reason or 'n/a'}",
                      view=TicketControlView(bot), allowed_mentions=discord.AllowedMentions(users=[i.user]))
        await i.followup.send(f"Ticket created: {ch.mention}", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)  # persistent: re-registered on startup
        self.bot = bot

    @discord.ui.button(label="Open ticket", style=discord.ButtonStyle.primary, custom_id="nexora:ticket:open")
    async def open_(self, i: discord.Interaction, _):
        await i.response.send_modal(TicketModal(self.bot))


class TicketControlView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _act(self, i: discord.Interaction, action: str):
        bot = self.bot
        t = await bot.store.get_ticket_by_channel(i.channel.id)
        if t is None:
            return await i.response.send_message("This isn't a tracked ticket.", ephemeral=True)
        staff = await _staff(bot, i)
        if not can_act(action, staff=staff, opener=(i.user.id == t.opener_id)):
            return await i.response.send_message("Staff only.", ephemeral=True)
        try:
            new = next_status(TicketStatus(t.status), action)
        except InvalidTransition as e:
            return await i.response.send_message(str(e), ephemeral=True)
        await i.response.defer()
        fields = {"status": new.value}
        if action == "claim":
            fields["claimed_by"] = i.user.id
        elif action == "unclaim":
            fields["claimed_by"] = None
        elif action == "close":
            fields["closed_at"] = utcnow()
        elif action == "reopen":
            fields["closed_at"] = None
        await bot.store.update_ticket(t.id, **fields)
        opener = i.guild.get_member(t.opener_id)
        if action == "close":
            await self._transcript(i, t)
            if opener:
                await i.channel.set_permissions(opener, view_channel=False, reason="ticket closed")
        elif action == "reopen" and opener:
            await i.channel.set_permissions(opener, view_channel=True, send_messages=True, read_message_history=True)
        await i.followup.send(f"Ticket {action}ed by {i.user.mention}.")

    async def _transcript(self, i, t):
        cfg = (await self.bot.settings_for(i.guild.id))["tickets"]
        if not cfg["transcripts"]:
            return
        lines = []
        async for m in i.channel.history(limit=1000, oldest_first=True):
            lines.append(f"[{m.created_at:%Y-%m-%d %H:%M:%S} UTC] {m.author} ({m.author.id}): {m.content}")
        data = "\n".join(lines).encode("utf-8")
        fname = f"ticket-{t.number:04d}.txt"
        try:  # local copy is best-effort (hosting disk may be read-only/ephemeral)
            path = Path(self.bot.cfg.transcript_dir) / str(i.guild.id)
            path.mkdir(parents=True, exist_ok=True)
            (path / fname).write_bytes(data)
        except OSError:
            log.warning("could not save transcript to disk")
        ch = i.guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
        if isinstance(ch, discord.TextChannel):  # only the staff-only log channel ever receives it
            await ch.send(f"Transcript for ticket #{t.number}", file=discord.File(io.BytesIO(data), filename=fname))

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="nexora:ticket:claim")
    async def claim(self, i, _): await self._act(i, "claim")

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="nexora:ticket:unclaim")
    async def unclaim(self, i, _): await self._act(i, "unclaim")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="nexora:ticket:close")
    async def close(self, i, _): await self._act(i, "close")


class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.add_view(TicketPanelView(bot))    # restart-safe: custom_ids are static, state lives in DB
        bot.add_view(TicketControlView(bot))

    ticket = app_commands.Group(name="ticket", description="Tickets", guild_only=True)

    @ticket.command(description="Post the ticket panel in this channel")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, i: discord.Interaction):
        ent = await self.bot.entitlements(i.guild.id)
        if ent.ticket_panels < 1:
            return await i.response.send_message("Tickets need an active plan or trial: use /plan.", ephemeral=True)
        await i.channel.send(embed=discord.Embed(title="Support", description="Press the button to open a ticket."),
                             view=TicketPanelView(self.bot))
        await i.response.send_message("Panel posted.", ephemeral=True)

    @ticket.command(description="Reopen this closed ticket (staff)")
    async def reopen(self, i: discord.Interaction):
        await TicketControlView(self.bot)._act(i, "reopen")

    @ticket.command(description="Get this ticket's transcript (staff)")
    async def transcript(self, i: discord.Interaction):
        t = await self.bot.store.get_ticket_by_channel(i.channel.id)
        if t is None or not await _staff(self.bot, i):
            return await i.response.send_message("Not available.", ephemeral=True)
        f = Path(self.bot.cfg.transcript_dir) / str(i.guild.id) / f"ticket-{t.number:04d}.txt"
        if not f.exists():
            return await i.response.send_message("No transcript yet (close the ticket first).", ephemeral=True)
        await i.response.send_message(file=discord.File(f), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Tickets(bot))
