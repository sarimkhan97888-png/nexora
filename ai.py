from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from nexora.core.clock import utcnow


class AI(commands.Cog):
    """The AI can only answer text. It has no access to moderation commands or other servers' data."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Ask the server's AI assistant")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id, i.user.id))
    async def ask(self, i: discord.Interaction, question: app_commands.Range[str, 1, 1500]):
        cfg = (await self.bot.settings_for(i.guild.id))["ai"]
        if cfg["channel_ids"] and i.channel.id not in cfg["channel_ids"]:
            return await i.response.send_message("The assistant isn't enabled in this channel.", ephemeral=True)
        ent = await self.bot.entitlements(i.guild.id)
        if ent.ai_requests_per_day < 1:
            return await i.response.send_message("The AI assistant needs an active plan or trial.", ephemeral=True)
        await i.response.defer()  # provider calls can take longer than 3 seconds
        r = await self.bot.ai.ask(i.guild.id, i.user.id, question, cfg, utcnow())
        await i.followup.send(r.text, allowed_mentions=discord.AllowedMentions.none(), ephemeral=not r.ok)


async def setup(bot):
    await bot.add_cog(AI(bot))
