import asyncio
import logging

import discord
from discord.ext import commands

from cogs.booster._custom_resource_ui import (
    BoosterActionResult,
    BoosterRoomCreatorView,
    RoomDesignDraft,
)
from cogs.booster._room_helpers import custom_room_category, custom_room_denial, private_room_overwrites
from cogs.economy._shop_rentals import paid_resource_denial
from cogs.roles._personal_roles import personal_resource_lock, resolve_personal_room


logger = logging.getLogger(__name__)


class BoosterCustomRoomCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = self.db["booster_custom_rooms"]
        self.shop_rooms = self.db["shop_custom_rooms"]

    def _is_booster(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    def _get_member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return personal_resource_lock(self.bot, guild_id, user_id, "custom_room")

    def _get_category(
        self,
        guild: discord.Guild,
    ) -> discord.CategoryChannel | None:
        return custom_room_category(self.bot, guild)

    def _base_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
        category: discord.CategoryChannel | None,
    ) -> str | None:
        if not self._is_booster(member):
            return "Bạn cần là Booster để dùng lệnh này."
        return custom_room_denial(self._get_bot_member(guild), category)

    async def _existing_room_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
    ) -> str | None:
        try:
            record = self.collection.find_one(
                {"guild_id": guild.id, "user_id": member.id}
            )
        except Exception:
            logger.exception(
                "Could not read booster room record for guild %s user %s.",
                guild.id,
                member.id,
            )
            return "Không thể kiểm tra custom room lúc này. Vui lòng thử lại."
        if record:
            channel_id = record.get("channel_id")
            existing = guild.get_channel(channel_id)
            if existing is None and isinstance(channel_id, int):
                try:
                    existing = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    existing = None
                except discord.HTTPException:
                    logger.exception(
                        "Could not verify stale booster room %s in guild %s.",
                        channel_id,
                        guild.id,
                    )
                    return "Không thể xác minh custom room hiện tại. Vui lòng thử lại."
            if existing is not None:
                return "Bạn đã có custom room rồi."
        try:
            denial = paid_resource_denial(self.db, guild.id, member.id, "custom_room")
            if denial:
                return denial
            record = self.shop_rooms.find_one({"guild_id": guild.id, "user_id": member.id})
            if await resolve_personal_room(guild, record) is not None:
                return "Bạn đã có phòng từ cửa hàng. Hãy dùng `shop use custom_room`."
        except Exception:
            logger.exception("Could not verify purchased room for booster")
            return "Không thể kiểm tra phòng lúc này. Vui lòng thử lại."
        return None

    def _private_overwrites(
        self,
        guild: discord.Guild,
        member: discord.Member,
        bot_member: discord.Member,
    ) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
        # Do not inherit category role allows: an allow for a broad role (for
        # example the Booster role) would expose every supposedly private room.
        return private_room_overwrites(guild, member, bot_member)

    async def _delete_untracked_room(
        self,
        channel: discord.VoiceChannel,
    ) -> bool:
        try:
            await channel.delete(reason="Rollback untracked booster custom room")
            return True
        except discord.HTTPException:
            logger.exception(
                "Could not delete untracked booster room %s in guild %s.",
                channel.id,
                channel.guild.id,
            )
            return False

    async def _create_custom_room(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        room_name: str,
        user_limit: int = 0,
    ) -> BoosterActionResult:
        room_name = room_name.strip()
        if not room_name:
            return BoosterActionResult(False, "Tên phòng không hợp lệ.")
        if len(room_name) > 100:
            return BoosterActionResult(False, "Tên phòng tối đa 100 ký tự.")
        if not 0 <= user_limit <= 99:
            return BoosterActionResult(False, "Giới hạn phòng phải từ 0 đến 99 người.")

        lock = self._get_member_lock(guild.id, member.id)
        async with lock:
            category = self._get_category(guild)
            denial = self._base_denial(guild, member, category)
            if denial or category is None:
                return BoosterActionResult(False, denial or "Không tìm thấy category.")
            denial = await self._existing_room_denial(guild, member)
            if denial:
                return BoosterActionResult(False, denial)

            bot_member = self._get_bot_member(guild)
            if bot_member is None:
                return BoosterActionResult(False, "Không tìm thấy bot trong server.")
            overwrites = self._private_overwrites(
                guild,
                member,
                bot_member,
            )
            try:
                channel = await guild.create_voice_channel(
                    name=room_name,
                    category=category,
                    overwrites=overwrites,
                    user_limit=user_limit,
                    reason=(
                        f"Booster custom room for {member} ({member.id})"
                    ),
                )
            except discord.Forbidden:
                return BoosterActionResult(
                    False,
                    "Bot không có quyền tạo phòng. Vui lòng kiểm tra quyền.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not create booster room in guild %s for user %s.",
                    guild.id,
                    member.id,
                )
                return BoosterActionResult(
                    False,
                    "Đã xảy ra lỗi khi tạo custom room.",
                )

            now = discord.utils.utcnow()
            try:
                self.collection.update_one(
                    {"guild_id": guild.id, "user_id": member.id},
                    {
                        "$set": {
                            "channel_id": channel.id,
                            "channel_name": channel.name,
                            "user_limit": user_limit,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            except Exception:
                logger.exception(
                    "Could not persist booster room %s in guild %s for user %s.",
                    channel.id,
                    guild.id,
                    member.id,
                )
                rolled_back = await self._delete_untracked_room(channel)
                if rolled_back:
                    return BoosterActionResult(
                        False,
                        "Không thể lưu custom room. Phòng vừa tạo đã được thu hồi; hãy thử lại.",
                    )
                return BoosterActionResult(
                    True,
                    (
                        "Không thể lưu hoặc thu hồi custom room vừa tạo "
                        f"(ID `{channel.id}`). Hãy báo staff để xóa thủ công và không thử lại lúc này."
                    ),
                )

            limit_text = (
                "không giới hạn người tham gia"
                if user_limit == 0
                else f"tối đa {user_limit} người"
            )
            return BoosterActionResult(
                True,
                (
                    f"Đã tạo custom room {channel.mention} cho {member.mention} "
                    f"({limit_text})."
                ),
            )

    async def _open_room_creator(self, ctx: commands.Context) -> None:
        category = self._get_category(ctx.guild)
        denial = self._base_denial(ctx.guild, ctx.author, category)
        if denial:
            await ctx.send(denial)
            return
        denial = await self._existing_room_denial(ctx.guild, ctx.author)
        if denial:
            await ctx.send(denial)
            return

        async def submitter(
            interaction: discord.Interaction,
            draft: RoomDesignDraft,
        ) -> BoosterActionResult:
            if interaction.guild is None or interaction.guild.id != ctx.guild.id:
                return BoosterActionResult(
                    False,
                    "Server đã thay đổi. Hãy gọi lại lệnh trong server ban đầu.",
                )
            return await self._create_custom_room(
                guild=interaction.guild,
                member=interaction.user,
                room_name=draft.room_name,
                user_limit=draft.user_limit,
            )

        view = BoosterRoomCreatorView(
            author_id=ctx.author.id,
            submitter=submitter,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    @commands.command(
        name="custom_room",
        aliases=["booster_room"],
        help=(
            "Tạo custom voice room; chạy không tham số để mở bảng thiết lập."
        ),
    )
    @commands.guild_only()
    async def custom_room(
        self,
        ctx: commands.Context,
        *,
        room_name: str | None = None,
    ) -> None:
        if room_name is None:
            await self._open_room_creator(ctx)
            return

        result = await self._create_custom_room(
            guild=ctx.guild,
            member=ctx.author,
            room_name=room_name,
        )
        await ctx.send(
            result.message,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @custom_room.error
    async def custom_room_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BoosterCustomRoomCog(bot))
