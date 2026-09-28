"""Category validation and private overwrites shared by paid and booster rooms."""

import discord
from discord.ext import commands


def custom_room_category(bot: commands.Bot, guild: discord.Guild) -> discord.CategoryChannel | None:
    try:
        category_id = int(getattr(bot, "global_vars", {}).get("BOOSTER_CUSTOM_VOICE_CATEGORY_ID", 0))
    except (TypeError, ValueError):
        return None
    channel = guild.get_channel(category_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


def custom_room_denial(
    bot_member: discord.Member | None, category: discord.CategoryChannel | None
) -> str | None:
    if bot_member is None or not bot_member.guild_permissions.manage_channels:
        return "Bot đang thiếu quyền Manage Channels."
    if not bot_member.guild_permissions.manage_roles:
        return "Bot đang thiếu quyền Manage Roles để tạo quyền riêng cho phòng."
    if category is None:
        return "Chưa cài đặt category cho custom room."
    permissions = category.permissions_for(bot_member)
    if not permissions.manage_channels or not permissions.manage_roles:
        return "Bot cần Manage Channels và Manage Roles trong category custom room."
    return None


def private_room_overwrites(
    guild: discord.Guild, member: discord.Member, bot_member: discord.Member
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Do not inherit category role allows that could expose private rooms."""
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        member: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True,
            manage_permissions=True, move_members=True,
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, manage_permissions=True,
        ),
    }
