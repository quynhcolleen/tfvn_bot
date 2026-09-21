"""Shared creation locks and saved-role lookup for personal custom roles."""

from __future__ import annotations

import asyncio
from typing import Any
from weakref import WeakValueDictionary

import discord
from discord.ext import commands


def personal_role_lock(
    bot: commands.Bot, guild_id: int, user_id: int
) -> asyncio.Lock:
    """Serialize booster and shop creation for one member on this bot."""
    return personal_resource_lock(bot, guild_id, user_id, "custom_role")


def personal_resource_lock(
    bot: commands.Bot, guild_id: int, user_id: int, item_type: str
) -> asyncio.Lock:
    """Share a resource lock across purchases, editors, boosters, and cleanup."""
    locks = getattr(bot, "_personal_resource_locks", None)
    if locks is None:
        locks = WeakValueDictionary()
        bot._personal_resource_locks = locks
    key = (guild_id, user_id, item_type)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


async def resolve_personal_role(
    guild: discord.Guild, record: dict[str, Any] | None
) -> discord.Role | None:
    """Verify a saved role through HTTP on cache misses; propagate API errors."""
    if not record:
        return None
    try:
        role_id = int(record.get("role_id"))
    except (TypeError, ValueError):
        return None
    role = guild.get_role(role_id)
    if role is not None:
        return role
    roles = await guild.fetch_roles()
    return next((role for role in roles if role.id == role_id), None)


async def resolve_personal_room(
    guild: discord.Guild, record: dict[str, Any] | None
) -> discord.VoiceChannel | None:
    """Resolve a tracked voice room, preserving uncertain or invalid records."""
    if not record:
        return None
    channel_id = int(record["channel_id"])
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            return None
    if not isinstance(channel, discord.VoiceChannel):
        raise ValueError("Saved custom room is not a voice channel")
    return channel
