from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from nexora.billing.domain import PLAN_DAYS, PlanCode, access_state
from nexora.billing.errors import BillingError
from nexora.core.clock import utcnow

log = logging.getLogger("nexora.pay")


class Pay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminders.start()

    def cog_unload(self):
        self.reminders.cancel()

    def _price_lines(self) -> str:
        rows = sorted(self.bot.cfg.prices.items(), key=lambda x: (x[0][1], PLAN_DAYS[x[0][0]]))
        return "\n".join(f"{PLAN_DAYS[p]} days ({p.value}): {a / 100:.2f} {cur}" for (p, cur), a in rows)

    @app_commands.command(description="Show this server's plan and prices")
    @app_commands.guild_only()
    async def plan(self, i: discord.Interaction):
        sub = await self.bot.store.get_subscription(i.guild.id)
        st = access_state(sub, utcnow(), self.bot.cfg.grace_days)
        exp = f"{sub.expires_at:%Y-%m-%d %H:%M} UTC" if sub and sub.expires_at else "-"
        await i.response.send_message(
            f"Status: **{st.value}** ({sub.plan.value if sub and sub.plan else 'free'}), expires: {exp}\n"
            f"Plans are prepaid and do **not** auto-renew.\n{self._price_lines()}\nUse `/buy` to get a payment link.", ephemeral=True)

    @app_commands.command(description="Get a secure payment link for this server")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(plan=[app_commands.Choice(name="7 days", value="weekly"),
                                app_commands.Choice(name="30 days", value="monthly"),
                                app_commands.Choice(name="365 days", value="yearly")])
    async def buy(self, i: discord.Interaction, plan: app_commands.Choice[str]):
        await i.response.defer(ephemeral=True)
        try:
            o = await self.bot.orders.create_checkout(guild_id=i.guild.id, user_id=i.user.id, plan=PlanCode(plan.value),
                                                      currency="INR", now=utcnow())
        except BillingError as e:
            return await i.followup.send(f"Could not create the payment link: {e}", ephemeral=True)
        await i.followup.send(f"Pay here (valid ~30 minutes): {o.short_url}\nAccess starts automatically after the payment "
                              "is verified (usually within a minute). Opening the link or sending a screenshot does nothing by itself.",
                              ephemeral=True)

    @tasks.loop(minutes=30)
    async def reminders(self):
        try:
            due = await self.bot.billing.due_reminders(utcnow())
        except Exception:  # noqa: BLE001
            return log.exception("reminder job failed")
        for sub, days in due:
            g = self.bot.get_guild(sub.guild_id)
            if g and g.owner:
                try:
                    await g.owner.send(f"NEXORA for **{g.name}** expires in about {days} day(s) "
                                       f"({sub.expires_at:%Y-%m-%d} UTC). Use /buy to renew. Plans do not auto-renew.")
                except discord.HTTPException:
                    pass

    @reminders.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Pay(bot))
