from __future__ import annotations

import discord

from nexora.core.clock import utcnow

DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members",
             "kick_members", "moderate_members", "manage_webhooks", "mention_everyone")


async def send_log(guild: discord.Guild, channel_id: int | None, title: str, desc: str, color=0x5865F2):
    if not channel_id:
        return
    ch = guild.get_channel(channel_id)
    if isinstance(ch, discord.TextChannel) and ch.permissions_for(guild.me).send_messages:
        try:
            await ch.send(embed=discord.Embed(title=title, description=desc[:3900], color=color, timestamp=utcnow()))
        except discord.HTTPException:
            pass


def role_assignable(guild: discord.Guild, role: discord.Role | None) -> str | None:
    """Explicit validation before giving a role automatically. Returns reason it is NOT safe, else None."""
    if role is None:
        return "role no longer exists"
    if role.managed or role.is_default():
        return "role is managed/default"
    if role >= guild.me.top_role:
        return "role is above my highest role"
    perms = role.permissions
    bad = [p for p in DANGEROUS if getattr(perms, p)]
    return f"role has dangerous permissions ({', '.join(bad)})" if bad else None
