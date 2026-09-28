"""Renewable paid private voice rooms, using the shared room designer."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.booster._custom_resource_ui import BoosterActionResult, BoosterRoomCreatorView, RoomDesignDraft
from cogs.booster._room_helpers import custom_room_category, custom_room_denial, private_room_overwrites
from cogs.economy._shop_helpers import CUSTOM_ROOM_ITEM_ID
from cogs.economy._shop_products import register_shop_product, unregister_shop_product
from cogs.economy._shop_rentals import MIGRATION_DENIAL, RentalAccess
from cogs.economy._shop_ui import NO_MENTIONS
from cogs.roles._personal_roles import personal_resource_lock, resolve_personal_room


logger = logging.getLogger(__name__)


class ShopRoomEditorView(BoosterRoomCreatorView):
    def __init__(self, cog: ShopCustomRoomCog, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cog = cog

    def stop(self) -> None:
        super().stop()
        self.cog._views.discard(self)

    async def on_timeout(self) -> None:
        self.stop()
        await super().on_timeout()


class ShopCustomRoomCog(commands.Cog):
    item_type = CUSTOM_ROOM_ITEM_ID

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.collection = bot.db["shop_custom_rooms"]
        self.booster_rooms = bot.db["booster_custom_rooms"]
        self._views: set[discord.ui.View] = set()
        self.rental = RentalAccess(bot, self.item_type, self._cleanup_room)
        try:
            self.collection.create_index(
                [("guild_id", 1), ("user_id", 1)], unique=True,
                name="guild_user_shop_custom_room_unique",
            )
        except PyMongoError:
            logger.exception("Could not index shop custom rooms")
        register_shop_product(self)

    async def cog_load(self) -> None:
        self.rental.start()

    def cog_unload(self) -> None:
        self.rental.stop()
        unregister_shop_product(self.item_type)
        for view in tuple(self._views):
            view.stop()

    def _base_denial(self, guild: discord.Guild) -> str | None:
        return custom_room_denial(guild.me, custom_room_category(self.bot, guild))

    async def _booster_denial(self, guild: discord.Guild, user_id: int) -> str | None:
        try:
            record = self.booster_rooms.find_one({"guild_id": guild.id, "user_id": user_id})
            if await resolve_personal_room(guild, record) is not None:
                return "Bạn đã có custom room từ Booster. Không thể mua thêm phòng."
        except (PyMongoError, discord.HTTPException, ValueError, KeyError):
            logger.exception("Could not verify booster room")
            return "Không thể xác minh phòng hiện tại. Vui lòng thử lại."
        return None

    async def buy_denial(
        self, guild: discord.Guild, member: discord.Member, item: dict[str, Any]
    ) -> str | None:
        if item.get("item_id") != CUSTOM_ROOM_ITEM_ID:
            return "Custom room phải dùng ID `custom_room`."
        if not self.rental.ensure_ready():
            return MIGRATION_DENIAL
        denial = self._base_denial(guild)
        if denial:
            return denial + " Bạn chưa bị trừ Trap Coin."
        return await self._booster_denial(guild, member.id)

    async def use_item(
        self, *, guild: discord.Guild, member: discord.Member,
        item: dict[str, Any], source: discord.Interaction | commands.Context,
    ) -> str | None:
        denial = self.rental.denial(guild.id, member.id) or self._base_denial(guild)
        if denial:
            return denial
        if isinstance(source, discord.Interaction) and not source.response.is_done():
            await source.response.defer()
        denial = await self._booster_denial(guild, member.id)
        if denial:
            return denial
        try:
            record = self.collection.find_one({"guild_id": guild.id, "user_id": member.id})
            channel = await resolve_personal_room(guild, record)
        except (PyMongoError, discord.HTTPException, ValueError, KeyError):
            logger.exception("Could not open purchased room")
            return "Không thể xác minh phòng hiện tại. Vui lòng thử lại."

        async def submitter(interaction: discord.Interaction, draft: RoomDesignDraft) -> BoosterActionResult:
            if interaction.guild is None or interaction.guild.id != guild.id:
                return BoosterActionResult(False, "Hãy mở lại shop trong server ban đầu.")
            actor = interaction.guild.get_member(interaction.user.id) or interaction.user
            return await self._save_room(
                interaction.guild, actor, draft,
                expected_channel_id=channel.id if channel else None,
            )

        view = ShopRoomEditorView(
            self, author_id=member.id, submitter=submitter, command_name="shop_custom_room",
            default_room_name=channel.name if channel else "",
            initial_user_limit=channel.user_limit if channel else 0,
            updating=channel is not None,
            owner_denial="Chỉ người mở shop được dùng bảng này.",
            owner_modal_denial="Chỉ người mở shop được chỉnh sửa phòng.",
            cancel_message="Đã hủy thiết kế custom room.",
            footer_note="Phòng thuê 30 ngày trong category đã cấu hình. Gia hạn qua shop.",
        )
        self._views.add(view)
        try:
            if isinstance(source, discord.Interaction):
                view.message = await source.edit_original_response(
                    content=None, embed=view.build_embed(), view=view, allowed_mentions=NO_MENTIONS,
                )
            else:
                view.message = await source.reply(
                    embed=view.build_embed(), view=view, mention_author=False, allowed_mentions=NO_MENTIONS,
                )
        except discord.HTTPException:
            logger.exception("Could not send shop room editor")
            view.stop()
            return "Không thể mở bảng thiết kế phòng. Vui lòng thử lại."
        return None

    async def _save_room(
        self, guild: discord.Guild, member: discord.Member, draft: RoomDesignDraft,
        *, expected_channel_id: int | None = None,
    ) -> BoosterActionResult:
        name = draft.room_name.strip()
        if not 1 <= len(name) <= 100 or not 0 <= draft.user_limit <= 99:
            return BoosterActionResult(False, "Tên phòng hoặc giới hạn người không hợp lệ.")
        lock = personal_resource_lock(self.bot, guild.id, member.id, self.item_type)
        async with lock:
            denial = self.rental.denial(guild.id, member.id) or self._base_denial(guild)
            if denial:
                return BoosterActionResult(False, denial)
            denial = await self._booster_denial(guild, member.id)
            if denial:
                return BoosterActionResult(False, denial)
            try:
                record = self.collection.find_one({"guild_id": guild.id, "user_id": member.id})
                channel = await resolve_personal_room(guild, record)
            except (PyMongoError, discord.HTTPException, ValueError, KeyError):
                logger.exception("Could not verify purchased room before submission")
                return BoosterActionResult(False, "Không thể xác minh phòng. Vui lòng thử lại.")
            if (channel.id if channel else None) != expected_channel_id:
                return BoosterActionResult(False, "Phòng đã thay đổi. Hãy mở lại shop.")
            denial = self.rental.denial(guild.id, member.id)
            if denial:
                return BoosterActionResult(False, denial)
            creating = channel is None
            try:
                if creating:
                    channel = await guild.create_voice_channel(
                        name=name, user_limit=draft.user_limit,
                        category=custom_room_category(self.bot, guild),
                        overwrites=private_room_overwrites(guild, member, guild.me),
                        reason=f"Shop custom room for {member.id}",
                    )
                else:
                    await channel.edit(name=name, user_limit=draft.user_limit, reason="Update shop custom room")
            except discord.HTTPException:
                logger.exception("Could not create or update shop room")
                return BoosterActionResult(False, "Bot không thể tạo/cập nhật phòng. Kiểm tra quyền và thử lại.")
            now = discord.utils.utcnow()
            try:
                self.collection.update_one(
                    {"guild_id": guild.id, "user_id": member.id},
                    {"$set": {"channel_id": channel.id, "channel_name": name,
                              "user_limit": draft.user_limit, "updated_at": now},
                     "$setOnInsert": {"created_at": now}},
                    upsert=True,
                )
            except PyMongoError:
                logger.exception("Could not persist shop room")
                if creating:
                    try:
                        await channel.delete(reason="Rollback untracked shop room")
                    except discord.HTTPException:
                        logger.exception("Could not rollback untracked shop room %s", channel.id)
                        return BoosterActionResult(True, f"Không thể lưu/thu hồi phòng `{channel.id}`. Hãy báo staff.")
                    return BoosterActionResult(False, "Không thể lưu phòng; phòng đã thu hồi. Hãy thử lại.")
                return BoosterActionResult(True, "Phòng đã cập nhật nhưng chưa thể đồng bộ dữ liệu.")
            return BoosterActionResult(True, f"Đã {'tạo' if creating else 'cập nhật'} phòng {channel.mention}.")

    async def _cleanup_room(self, guild: discord.Guild, user_id: int, *, reason: str) -> bool:
        try:
            record = self.collection.find_one({"guild_id": guild.id, "user_id": user_id})
            channel = await resolve_personal_room(guild, record)
            if channel is not None:
                try:
                    await channel.delete(reason=reason)
                except discord.NotFound:
                    pass
            self.collection.delete_one({"guild_id": guild.id, "user_id": user_id})
            return True
        except (PyMongoError, discord.HTTPException, ValueError, KeyError):
            logger.exception("Could not delete shop room for guild %s user %s", guild.id, user_id)
            return False

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        lock = personal_resource_lock(self.bot, member.guild.id, member.id, self.item_type)
        async with lock:
            await self._cleanup_room(member.guild, member.id, reason="Shop room owner left")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCustomRoomCog(bot))
