"""Shop product: buy and design a personal custom role with Trap Coin."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands
from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from cogs.booster._custom_resource_ui import (
    BoosterActionResult,
    BoosterRoleEditorView,
    RoleDesignDraft,
)
from cogs.booster._role_colors import RoleColorSpec
from cogs.economy._shop_helpers import CUSTOM_ROLE_ITEM_ID, ITEM_TYPE_CUSTOM_ROLE
from cogs.economy._shop_products import register_shop_product, unregister_shop_product
from cogs.economy._shop_ui import NO_MENTIONS


logger = logging.getLogger(__name__)

SHOP_CUSTOM_ROLES_COLLECTION = "shop_custom_roles"
BOOSTER_CUSTOM_ROLES_COLLECTION = "booster_custom_roles"
OWNER_DENIAL = "Chỉ người mở cửa hàng mới dùng được bảng thiết kế role."
OWNER_MODAL_DENIAL = "Chỉ người mở cửa hàng mới chỉnh sửa thiết kế role."
CANCEL_MESSAGE = "Đã hủy thiết kế custom role."
FOOTER_NOTE = "Custom role đã mua từ cửa hàng Trap Coin."


class ShopCustomRoleCog(commands.Cog):
    """Create and update a paid personal role after a shop purchase."""

    item_type = ITEM_TYPE_CUSTOM_ROLE

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = self.db[SHOP_CUSTOM_ROLES_COLLECTION]
        self.booster_roles = self.db[BOOSTER_CUSTOM_ROLES_COLLECTION]
        self._member_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._views: set[discord.ui.View] = set()
        self._unloading = False
        self._ensure_indexes()
        register_shop_product(self)

    def cog_unload(self) -> None:
        self._unloading = True
        unregister_shop_product(self.item_type)
        for view in tuple(self._views):
            view.stop()
        self._views.clear()

    def _ensure_indexes(self) -> None:
        try:
            self.collection.create_index(
                [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="guild_user_shop_custom_role_unique",
            )
        except PyMongoError:
            logger.exception("Failed to create shop custom role indexes")

    def _get_member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._member_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    def _get_anchor_role(self, guild: discord.Guild) -> discord.Role | None:
        if not hasattr(self.bot, "global_vars"):
            return None
        anchor_value = self.bot.global_vars.get("BOOSTER_CUSTOM_ROLE_ANCHOR_ID")
        if not anchor_value:
            return None
        try:
            role_id = int(anchor_value)
        except (TypeError, ValueError):
            return None
        return guild.get_role(role_id)

    def _base_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str | None:
        bot_member = self._get_bot_member(guild)
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            return "Bot đang thiếu quyền Manage Roles."
        return None

    def _live_personal_role(
        self,
        guild: discord.Guild,
        record: dict[str, Any] | None,
    ) -> discord.Role | None:
        if not record:
            return None
        role_id = record.get("role_id")
        if not isinstance(role_id, int):
            try:
                role_id = int(role_id)
            except (TypeError, ValueError):
                return None
        return guild.get_role(role_id)

    def buy_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
    ) -> str | None:
        if str(item.get("item_id")) != CUSTOM_ROLE_ITEM_ID:
            return "Custom role trong shop phải dùng ID `custom_role`."
        denial = self._base_denial(guild, member)
        if denial:
            return denial + " Bạn chưa bị trừ Trap Coin."
        booster_record = self.booster_roles.find_one(
            {"guild_id": guild.id, "user_id": member.id}
        )
        if self._live_personal_role(guild, booster_record) is not None:
            return (
                "Bạn đã có custom role từ Booster. Không cần mua thêm từ cửa hàng."
            )
        shop_record = self.collection.find_one(
            {"guild_id": guild.id, "user_id": member.id}
        )
        if self._live_personal_role(guild, shop_record) is not None:
            return "Bạn đã có custom role từ cửa hàng."
        return None

    async def use_item(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
        source: discord.Interaction | commands.Context,
    ) -> str | None:
        denial = self._base_denial(guild, member)
        if denial:
            return denial
        record = self.collection.find_one(
            {"guild_id": guild.id, "user_id": member.id}
        )
        role = self._live_personal_role(guild, record)
        updating = role is not None
        return await self._open_role_editor(
            source,
            member=member,
            existing_role=role,
            record=record,
            updating=updating,
        )

    async def _open_role_editor(
        self,
        source: discord.Interaction | commands.Context,
        *,
        member: discord.Member,
        existing_role: discord.Role | None,
        record: dict[str, Any] | None,
        updating: bool,
    ) -> str | None:
        initial_color = None
        default_name = ""
        if existing_role is not None and record is not None:
            initial_color = self._record_color_spec(record, existing_role)
            default_name = str(record.get("role_name") or existing_role.name)

        async def submitter(
            interaction: discord.Interaction,
            draft: RoleDesignDraft,
        ) -> BoosterActionResult:
            if interaction.guild is None or interaction.guild.id != member.guild.id:
                return BoosterActionResult(
                    False,
                    "Server đã thay đổi. Hãy mở lại cửa hàng trong server ban đầu.",
                )
            actor = interaction.guild.get_member(interaction.user.id)
            if actor is None:
                actor = interaction.user
            if getattr(actor, "id", None) is None:
                return BoosterActionResult(
                    False,
                    "Cửa hàng chỉ dùng trong server.",
                )
            if updating and existing_role is not None:
                return await self._update_custom_role(
                    guild=interaction.guild,
                    member=actor,
                    role=existing_role,
                    color_spec=draft.color_spec,
                    role_name=draft.role_name,
                )
            return await self._create_custom_role(
                guild=interaction.guild,
                member=actor,
                color_spec=draft.color_spec,
                role_name=draft.role_name,
            )

        view = BoosterRoleEditorView(
            author_id=member.id,
            command_name="update_custom_role" if updating else "shop_custom_role",
            submitter=submitter,
            default_role_name=default_name,
            initial_color_spec=initial_color,
            owner_denial=OWNER_DENIAL,
            owner_modal_denial=OWNER_MODAL_DENIAL,
            cancel_message=CANCEL_MESSAGE,
            footer_note=FOOTER_NOTE,
        )
        self._views.add(view)

        embed = view.build_embed()
        if isinstance(source, discord.Interaction):
            try:
                if source.response.is_done():
                    message = await source.followup.send(
                        embed=embed,
                        view=view,
                        ephemeral=False,
                        allowed_mentions=NO_MENTIONS,
                    )
                    if isinstance(message, discord.Message):
                        view.message = message
                else:
                    await source.response.edit_message(
                        content=None,
                        embed=embed,
                        view=view,
                        allowed_mentions=NO_MENTIONS,
                    )
                    view.message = source.message
            except discord.HTTPException:
                logger.exception("Could not open shop custom role editor")
                view.stop()
                self._views.discard(view)
                return "Không thể mở bảng thiết kế role. Vui lòng thử lại."
            return None

        try:
            view.message = await source.reply(
                embed=embed,
                view=view,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.exception("Could not send shop custom role editor")
            view.stop()
            self._views.discard(view)
            return "Không thể mở bảng thiết kế role. Vui lòng thử lại."
        return None

    def _record_color_spec(
        self,
        record: dict[str, Any],
        role: discord.Role,
    ) -> RoleColorSpec:
        primary_value = record.get("primary_color", role.colour.value)
        secondary_value = record.get("secondary_color")
        try:
            primary = discord.Color(int(primary_value))
        except (TypeError, ValueError):
            primary = role.colour
        try:
            secondary = (
                discord.Color(int(secondary_value))
                if secondary_value is not None
                else None
            )
        except (TypeError, ValueError):
            secondary = role.secondary_colour
        return RoleColorSpec(primary=primary, secondary=secondary)

    async def _delete_untracked_role(self, role: discord.Role) -> bool:
        try:
            await role.delete(reason="Rollback untracked shop custom role")
            return True
        except discord.HTTPException:
            logger.exception(
                "Could not delete untracked shop role %s in guild %s.",
                role.id,
                role.guild.id,
            )
            return False

    async def _place_under_anchor(
        self,
        guild: discord.Guild,
        role: discord.Role,
        bot_member: discord.Member | None,
    ) -> str | None:
        anchor_role = self._get_anchor_role(guild)
        if anchor_role is None:
            return None
        if (
            anchor_role.is_default()
            or anchor_role.managed
            or (bot_member is not None and anchor_role >= bot_member.top_role)
        ):
            return "Không thể đặt role dưới anchor đã cấu hình."
        target_position = max(anchor_role.position - 1, 1)
        try:
            await role.edit(
                position=target_position,
                reason="Place shop custom role under anchor",
            )
        except discord.Forbidden:
            return "Bot không có quyền đặt thứ bậc role."
        except discord.HTTPException:
            logger.exception(
                "Could not position shop role %s in guild %s.",
                role.id,
                guild.id,
            )
            return "Không thể cập nhật thứ bậc role."
        return None

    async def _create_custom_role(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        color_spec: RoleColorSpec,
        role_name: str,
    ) -> BoosterActionResult:
        role_name = role_name.strip()
        if not role_name:
            return BoosterActionResult(False, "Tên role không hợp lệ.")
        if len(role_name) > 100:
            return BoosterActionResult(False, "Tên role tối đa 100 ký tự.")

        lock = self._get_member_lock(guild.id, member.id)
        async with lock:
            denial = self._base_denial(guild, member)
            if denial:
                return BoosterActionResult(False, denial)

            existing = self.collection.find_one(
                {"guild_id": guild.id, "user_id": member.id}
            )
            live_role = self._live_personal_role(guild, existing)
            if live_role is not None:
                return BoosterActionResult(
                    False,
                    "Bạn đã có custom role. Hãy dùng lại nút Dùng để cập nhật.",
                )

            try:
                role = await guild.create_role(
                    name=role_name,
                    mentionable=False,
                    reason=f"Shop custom role for {member} ({member.id})",
                    **color_spec.create_kwargs(),
                )
            except discord.Forbidden:
                return BoosterActionResult(
                    False,
                    "Bot không có quyền tạo role. Vui lòng kiểm tra quyền và thứ bậc role.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not create shop role in guild %s for user %s.",
                    guild.id,
                    member.id,
                )
                return BoosterActionResult(False, "Đã xảy ra lỗi khi tạo role.")

            now = discord.utils.utcnow()
            try:
                self.collection.update_one(
                    {"guild_id": guild.id, "user_id": member.id},
                    {
                        "$set": {
                            "role_id": role.id,
                            "role_name": role.name,
                            **color_spec.record_fields(),
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            except Exception:
                logger.exception(
                    "Could not persist shop role %s in guild %s for user %s.",
                    role.id,
                    guild.id,
                    member.id,
                )
                rolled_back = await self._delete_untracked_role(role)
                if rolled_back:
                    return BoosterActionResult(
                        False,
                        "Không thể lưu custom role. Role vừa tạo đã được thu hồi; hãy thử lại.",
                    )
                return BoosterActionResult(
                    True,
                    (
                        "Không thể lưu hoặc thu hồi custom role vừa tạo "
                        f"(ID `{role.id}`). Hãy báo staff để xóa thủ công và không thử lại lúc này."
                    ),
                )

            warnings: list[str] = []
            bot_member = self._get_bot_member(guild)
            anchor_warning = await self._place_under_anchor(guild, role, bot_member)
            if anchor_warning:
                warnings.append(anchor_warning)

            if bot_member is None or role >= bot_member.top_role:
                warnings.append(
                    "Role đã được tạo nhưng bot chưa thể gán do thứ bậc role."
                )
            else:
                try:
                    await member.add_roles(
                        role,
                        reason="Assign shop custom role",
                    )
                except discord.Forbidden:
                    warnings.append(
                        "Role đã được tạo nhưng bot chưa có quyền gán role."
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not assign shop role %s in guild %s to user %s.",
                        role.id,
                        guild.id,
                        member.id,
                    )
                    warnings.append(
                        "Role đã được tạo nhưng chưa thể gán do lỗi Discord."
                    )

            message = f"Đã tạo custom role {role.mention} từ cửa hàng."
            if warnings:
                message += "\n⚠️ " + " ".join(warnings)
            return BoosterActionResult(True, message)

    async def _update_custom_role(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        role: discord.Role,
        color_spec: RoleColorSpec,
        role_name: str,
    ) -> BoosterActionResult:
        role_name = role_name.strip()
        if not role_name:
            return BoosterActionResult(False, "Tên role không hợp lệ.")
        if len(role_name) > 100:
            return BoosterActionResult(False, "Tên role tối đa 100 ký tự.")

        lock = self._get_member_lock(guild.id, member.id)
        async with lock:
            denial = self._base_denial(guild, member)
            if denial:
                return BoosterActionResult(False, denial)
            if role.is_default() or role.managed:
                return BoosterActionResult(
                    False,
                    "Role đã lưu không thể chỉnh sửa vì do Discord quản lý.",
                )
            bot_member = self._get_bot_member(guild)
            if bot_member is None or role >= bot_member.top_role:
                return BoosterActionResult(
                    False,
                    "Bot không thể chỉnh sửa role này vì thứ bậc cao hơn bot.",
                )

            try:
                await role.edit(
                    name=role_name,
                    reason=f"Shop custom role update for {member} ({member.id})",
                    **color_spec.edit_kwargs(),
                )
            except discord.Forbidden:
                return BoosterActionResult(
                    False,
                    "Bot không có quyền chỉnh sửa role. Vui lòng kiểm tra quyền và thứ bậc role.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not update shop role %s in guild %s for user %s.",
                    role.id,
                    guild.id,
                    member.id,
                )
                return BoosterActionResult(False, "Đã xảy ra lỗi khi cập nhật role.")

            warnings: list[str] = []
            if role not in member.roles:
                try:
                    await member.add_roles(
                        role,
                        reason="Assign shop custom role",
                    )
                except discord.Forbidden:
                    warnings.append(
                        "Role đã cập nhật nhưng bot chưa có quyền gán lại cho bạn."
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not reassign shop role %s in guild %s to user %s.",
                        role.id,
                        guild.id,
                        member.id,
                    )
                    warnings.append(
                        "Role đã cập nhật nhưng chưa thể gán lại do lỗi Discord."
                    )

            now = discord.utils.utcnow()
            try:
                self.collection.update_one(
                    {"guild_id": guild.id, "user_id": member.id},
                    {
                        "$set": {
                            "role_name": role_name,
                            **color_spec.record_fields(),
                            "updated_at": now,
                        }
                    },
                )
            except Exception:
                logger.exception(
                    "Could not persist shop role update %s in guild %s for user %s.",
                    role.id,
                    guild.id,
                    member.id,
                )
                warnings.append(
                    "Role đã cập nhật trên Discord nhưng chưa thể đồng bộ dữ liệu."
                )

            message = f"Đã cập nhật custom role: {role.mention}"
            if warnings:
                message += "\n⚠️ " + " ".join(warnings)
            return BoosterActionResult(True, message)

    async def _cleanup_member(
        self,
        guild: discord.Guild,
        user_id: int,
        *,
        reason: str,
    ) -> None:
        try:
            record = self.collection.find_one(
                {"guild_id": guild.id, "user_id": user_id}
            )
        except PyMongoError:
            logger.exception(
                "Could not read shop custom role for guild %s user %s",
                guild.id,
                user_id,
            )
            return
        if not record:
            return
        role = self._live_personal_role(guild, record)
        if role is not None:
            try:
                await role.delete(reason=reason)
            except discord.Forbidden:
                logger.warning(
                    "Missing permission to delete shop role %s in guild %s",
                    role.id,
                    guild.id,
                )
                return
            except discord.HTTPException:
                logger.warning(
                    "Failed to delete shop role %s in guild %s",
                    role.id,
                    guild.id,
                )
                return
        try:
            self.collection.delete_one(
                {"guild_id": guild.id, "user_id": user_id}
            )
        except PyMongoError:
            logger.exception(
                "Could not delete shop custom role record for guild %s user %s",
                guild.id,
                user_id,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._cleanup_member(
            member.guild,
            member.id,
            reason="Shop custom role cleanup after member left",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCustomRoleCog(bot))
