"""Built-in catalog products: sellable Discord roles and profile badges."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from cogs.roles._role_safety import dangerous_permission_names


logger = logging.getLogger(__name__)


def assignable_role(
    guild: discord.Guild,
    role_id: object,
) -> tuple[discord.Role | None, str | None]:
    """Return a role the bot can safely assign, or a Vietnamese denial."""
    try:
        resolved_id = int(role_id or 0)
    except (TypeError, ValueError):
        resolved_id = 0
    role = guild.get_role(resolved_id)
    bot_member = guild.me
    if (
        role is None
        or role.is_default()
        or role.managed
        or dangerous_permission_names(role.permissions)
        or bot_member is None
        or role >= bot_member.top_role
    ):
        return None, (
            "Role của vật phẩm này hiện không an toàn hoặc bot không thể gán."
        )
    return role, None


class CatalogProduct:
    """Role or badge listings stored in ``shop_items``."""

    def __init__(self, item_type: str, store: Any) -> None:
        self.item_type = item_type
        self.store = store

    def buy_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
    ) -> str | None:
        if self.item_type != "role":
            return None
        role, denial = assignable_role(guild, item.get("role_id"))
        if denial or role is None:
            return (
                denial
                or "Role của vật phẩm này hiện không an toàn hoặc bot không thể gán."
            ) + " Bạn chưa bị trừ Trap Coin."
        if role in member.roles:
            return "Bạn đã có role của vật phẩm này."
        return None

    async def use_item(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
        source: discord.Interaction | commands.Context,
    ) -> str | None:
        if self.item_type == "badge":
            self.store.set_active_badge(
                user_id=member.id,
                guild_id=guild.id,
                item_id=str(item["item_id"]),
                name=str(item["name"]),
            )
            return f"Đã trang bị badge **{item['name']}**."

        try:
            role_id = int(item.get("role_id") or 0)
        except (TypeError, ValueError):
            role_id = 0
        role = guild.get_role(role_id)
        bot_member = guild.me
        if role is None:
            return "Role của vật phẩm này không còn tồn tại."
        if dangerous_permission_names(role.permissions):
            return "Role này có quyền quản trị và không thể dùng qua shop."
        if role.managed or bot_member is None or role >= bot_member.top_role:
            return "Bot không thể gán role này do thứ bậc hoặc role được quản lý."
        if role in member.roles:
            return f"Bạn đang có role {role.mention} rồi."

        try:
            await member.add_roles(role, reason="Use purchased shop role")
        except discord.Forbidden:
            return "Bot không có quyền gán role này."
        except discord.HTTPException:
            logger.exception(
                "Could not assign shop role %s in guild %s to user %s",
                role.id,
                guild.id,
                member.id,
            )
            return "Discord từ chối cập nhật role. Vui lòng thử lại."
        return f"Đã kích hoạt role {role.mention}."
