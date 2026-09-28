import asyncio
import logging

import discord
from discord.ext import commands

from cogs.booster._custom_resource_ui import (
    BoosterActionResult,
    BoosterRoleEditorView,
    RoleDesignDraft,
)
from cogs.booster._role_colors import RoleColorSpec, parse_role_color_args
from cogs.roles._personal_roles import personal_role_lock, resolve_personal_role
from cogs.economy._shop_rentals import paid_resource_denial


logger = logging.getLogger(__name__)


class BoosterCustomRoleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = self.db["booster_custom_roles"]
        self.shop_roles = self.db["shop_custom_roles"]

    def _is_booster(self, member: discord.Member) -> bool:
        return member.premium_since is not None

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if not self.bot.user:
            return None
        return guild.get_member(self.bot.user.id)

    def _get_member_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return personal_role_lock(self.bot, guild_id, user_id)

    def _get_png_attachment(
        self,
        message: discord.Message,
    ) -> discord.Attachment | None:
        for attachment in message.attachments:
            if attachment.content_type == "image/png":
                return attachment
            if attachment.filename.lower().endswith(".png"):
                return attachment
        return None

    def _attachment_result(
        self,
        message: discord.Message,
    ) -> tuple[discord.Attachment | None, str | None]:
        if not message.attachments:
            return None, None
        attachment = self._get_png_attachment(message)
        if attachment is None:
            return None, "Vui lòng đính kèm file PNG nếu muốn đặt icon role."
        if attachment.size > 256 * 1024:
            return None, "Icon PNG tối đa 256KB."
        return attachment, None

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
        if not self._is_booster(member):
            return "Bạn cần là Booster để dùng lệnh này."
        bot_member = self._get_bot_member(guild)
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            return "Bot đang thiếu quyền Manage Roles."
        return None

    async def _existing_role_denial(
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
                "Could not read booster role record for guild %s user %s.",
                guild.id,
                member.id,
            )
            return "Không thể kiểm tra custom role lúc này. Vui lòng thử lại."
        if record:
            try:
                role = await resolve_personal_role(guild, record)
            except discord.HTTPException:
                logger.exception(
                    "Could not verify booster role in guild %s for user %s.",
                    guild.id,
                    member.id,
                )
                return "Không thể xác minh custom role hiện tại. Vui lòng thử lại."
            if role is not None:
                return (
                    "Bạn đã có custom role. Hãy dùng lệnh "
                    "`update_custom_role` để cập nhật."
                )
        try:
            denial = paid_resource_denial(self.db, guild.id, member.id, "custom_role")
            if denial:
                return denial
            shop_record = self.shop_roles.find_one(
                {"guild_id": guild.id, "user_id": member.id}
            )
            shop_role = await resolve_personal_role(guild, shop_record)
        except Exception:
            logger.exception(
                "Could not read shop custom role for guild %s user %s.",
                guild.id,
                member.id,
            )
            return "Không thể kiểm tra custom role lúc này. Vui lòng thử lại."
        if shop_role is not None:
            return (
                "Bạn đã có custom role từ cửa hàng. "
                "Hãy dùng `shop use custom_role` để cập nhật."
            )
        return None

    async def _read_icon(
        self,
        attachment: discord.Attachment | None,
    ) -> tuple[bytes | None, str | None]:
        if attachment is None:
            return None, None
        try:
            return await attachment.read(), None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Could not read booster custom-role icon attachment.")
            return None, "Không thể đọc icon PNG. Vui lòng đính kèm lại và thử lại."

    async def _delete_untracked_role(self, role: discord.Role) -> bool:
        try:
            await role.delete(reason="Rollback untracked booster custom role")
            return True
        except discord.HTTPException:
            logger.exception(
                "Could not delete untracked booster role %s in guild %s.",
                role.id,
                role.guild.id,
            )
            return False

    async def _create_custom_role(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        color_spec: RoleColorSpec,
        role_name: str,
        icon_attachment: discord.Attachment | None,
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
            denial = await self._existing_role_denial(guild, member)
            if denial:
                return BoosterActionResult(False, denial)

            icon_bytes, icon_error = await self._read_icon(icon_attachment)
            if icon_error:
                return BoosterActionResult(False, icon_error)

            try:
                role = await guild.create_role(
                    name=role_name,
                    mentionable=False,
                    display_icon=icon_bytes,
                    reason=(
                        f"Booster custom role for {member} ({member.id})"
                    ),
                    **color_spec.create_kwargs(),
                )
            except discord.Forbidden:
                return BoosterActionResult(
                    False,
                    "Bot không có quyền tạo role. Vui lòng kiểm tra quyền và thứ bậc role.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not create booster role in guild %s for user %s.",
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
                    "Could not persist booster role %s in guild %s for user %s.",
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
            anchor_role = self._get_anchor_role(guild)
            if anchor_role:
                if (
                    anchor_role.is_default()
                    or anchor_role.managed
                    or (bot_member is not None and anchor_role >= bot_member.top_role)
                ):
                    warnings.append(
                        "Không thể đặt role dưới anchor đã cấu hình."
                    )
                else:
                    target_position = max(anchor_role.position - 1, 1)
                    try:
                        await role.edit(
                            position=target_position,
                            reason="Place booster custom role under anchor",
                        )
                    except discord.Forbidden:
                        warnings.append("Bot không có quyền đặt thứ bậc role.")
                    except discord.HTTPException:
                        logger.exception(
                            "Could not position booster role %s in guild %s.",
                            role.id,
                            guild.id,
                        )
                        warnings.append("Không thể cập nhật thứ bậc role.")

            if bot_member is None or role >= bot_member.top_role:
                warnings.append(
                    "Role đã được tạo nhưng bot chưa thể gán do thứ bậc role."
                )
            else:
                try:
                    await member.add_roles(
                        role,
                        reason="Assign booster custom role",
                    )
                except discord.Forbidden:
                    warnings.append(
                        "Role đã được tạo nhưng bot chưa có quyền gán role."
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not assign booster role %s in guild %s to user %s.",
                        role.id,
                        guild.id,
                        member.id,
                    )
                    warnings.append(
                        "Role đã được tạo nhưng chưa thể gán do lỗi Discord."
                    )

            message = f"Đã tạo custom role {role.mention} cho {member.mention}."
            if warnings:
                message += "\n⚠️ " + " ".join(warnings)
            return BoosterActionResult(True, message)

    async def _open_role_editor(
        self,
        ctx: commands.Context,
        icon_attachment: discord.Attachment | None,
    ) -> None:
        denial = self._base_denial(ctx.guild, ctx.author)
        if denial:
            await ctx.send(denial)
            return
        denial = await self._existing_role_denial(ctx.guild, ctx.author)
        if denial:
            await ctx.send(denial)
            return

        async def submitter(
            interaction: discord.Interaction,
            draft: RoleDesignDraft,
        ) -> BoosterActionResult:
            if interaction.guild is None or interaction.guild.id != ctx.guild.id:
                return BoosterActionResult(
                    False,
                    "Server đã thay đổi. Hãy gọi lại lệnh trong server ban đầu.",
                )
            return await self._create_custom_role(
                guild=interaction.guild,
                member=interaction.user,
                color_spec=draft.color_spec,
                role_name=draft.role_name,
                icon_attachment=icon_attachment,
            )

        view = BoosterRoleEditorView(
            author_id=ctx.author.id,
            command_name="custom_role",
            submitter=submitter,
            icon_attached=icon_attachment is not None,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    @commands.command(
        name="custom_role",
        aliases=["booster_role"],
        help=(
            "Tạo custom role cho booster; chạy không tham số để mở bảng chọn màu."
        ),
    )
    @commands.guild_only()
    async def custom_role(
        self,
        ctx: commands.Context,
        color_hex: str | None = None,
        *,
        role_name: str | None = None,
    ) -> None:
        icon_attachment, attachment_error = self._attachment_result(ctx.message)
        if attachment_error:
            await ctx.send(attachment_error)
            return

        if color_hex is None:
            if role_name is not None:
                await ctx.send(
                    f"Cách dùng: `{ctx.clean_prefix}custom_role <màu> <tên role>` "
                    "hoặc gọi lệnh không tham số để mở bảng chọn."
                )
                return
            await self._open_role_editor(ctx, icon_attachment)
            return

        if role_name is None:
            await ctx.send(
                f"Cách dùng: `{ctx.clean_prefix}custom_role <màu> <tên role>` "
                "hoặc gọi lệnh không tham số để mở bảng chọn."
            )
            return

        color_spec, parsed_role_name = parse_role_color_args(color_hex, role_name)
        if color_spec is None:
            await ctx.send(
                "Màu không hợp lệ. Dùng #RRGGBB hoặc #RRGGBB,#RRGGBB cho gradient."
            )
            return

        result = await self._create_custom_role(
            guild=ctx.guild,
            member=ctx.author,
            color_spec=color_spec,
            role_name=parsed_role_name,
            icon_attachment=icon_attachment,
        )
        await ctx.send(
            result.message,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @custom_role.error
    async def custom_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh này chỉ dùng trong server.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BoosterCustomRoleCog(bot))
