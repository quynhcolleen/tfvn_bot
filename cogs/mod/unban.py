import logging
import re

import discord
from discord.ext import commands

from cogs.mod._case_helpers import (
    case_suffix,
    clean_case_reason,
    format_audit_reason,
    record_case,
)
from cogs.mod._interaction_ui import PrefixModerationContext, run_prefix_action
from cogs.mod._unban_ui import (
    REINVITE_MAX_AGE_SECONDS,
    UnbanActionResult,
    UnbanRequest,
    UnbanWorkflowView,
    unban_permission_denial,
)


logger = logging.getLogger(__name__)

UNBAN_COMMAND_COOLDOWN_SECONDS = 5
MAX_DISCORD_SNOWFLAKE = (1 << 64) - 1
USER_MENTION_PATTERN = re.compile(r"^<@!?(?P<user_id>\d+)>$")

InviteChannel = (
    discord.TextChannel
    | discord.VoiceChannel
    | discord.StageChannel
    | discord.ForumChannel
)


class UnbanTargetLookupError(ValueError):
    """Raised when an unban target cannot be safely resolved."""


def parse_unban_user_id(value: str) -> int:
    """Parse a raw user ID or user mention into a Discord snowflake."""
    stripped = value.strip()
    match = USER_MENTION_PATTERN.fullmatch(stripped)
    candidate = match.group("user_id") if match is not None else stripped
    if not candidate.isdecimal():
        raise ValueError("Unban target must be a user ID or mention")

    user_id = int(candidate)
    if not 0 < user_id <= MAX_DISCORD_SNOWFLAKE:
        raise ValueError("Unban target is outside the Discord snowflake range")
    return user_id


def _safe_text(value: str, *, max_length: int = 1200) -> str:
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(value))
    if len(escaped) <= max_length:
        return escaped
    return f"{escaped[: max_length - 1]}…"


class UnbanCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    async def _fetch_replied_message(
        ctx: commands.Context,
        reference: discord.MessageReference,
    ) -> discord.Message:
        if reference.message_id is None:
            raise UnbanTargetLookupError("Tin nhắn được reply không còn khả dụng.")
        if reference.channel_id != ctx.channel.id:
            raise UnbanTargetLookupError(
                "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
            )
        try:
            return await ctx.channel.fetch_message(reference.message_id)
        except discord.NotFound as exc:
            raise UnbanTargetLookupError(
                "Không tìm thấy tin nhắn được reply. Tin nhắn có thể đã bị xóa."
            ) from exc
        except discord.Forbidden as exc:
            raise UnbanTargetLookupError(
                "Bot không có quyền đọc lịch sử tin nhắn trong kênh này."
            ) from exc
        except discord.HTTPException as exc:
            logger.exception(
                "Could not fetch replied message for unban message=%s",
                reference.message_id,
            )
            raise UnbanTargetLookupError(
                "Không thể tải tin nhắn được reply lúc này. Hãy thử lại sau."
            ) from exc

    async def _resolve_reply_user_id(self, ctx: commands.Context) -> int:
        reference = ctx.message.reference
        if reference is None:
            raise UnbanTargetLookupError(
                f"Hãy nhập user ID hoặc reply tin nhắn bằng "
                f"`{ctx.clean_prefix}unban`."
            )

        resolved = reference.resolved
        if isinstance(resolved, discord.DeletedReferencedMessage):
            raise UnbanTargetLookupError(
                "Tin nhắn được reply đã bị xóa nên không thể xác định người dùng."
            )
        if isinstance(resolved, discord.Message):
            message = resolved
        else:
            cached = reference.cached_message
            message = (
                cached
                if cached is not None
                else await self._fetch_replied_message(ctx, reference)
            )

        if (
            ctx.guild is None
            or message.guild is None
            or message.guild.id != ctx.guild.id
        ):
            raise UnbanTargetLookupError(
                "Không thể chọn người dùng từ tin nhắn ở server khác."
            )
        if message.channel.id != ctx.channel.id:
            raise UnbanTargetLookupError(
                "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
            )
        if getattr(message, "webhook_id", None) is not None:
            raise UnbanTargetLookupError(
                "Không thể unban tác giả của tin nhắn webhook."
            )
        return message.author.id

    @staticmethod
    def _as_invite_channel(channel: object) -> InviteChannel | None:
        if isinstance(channel, discord.Thread):
            channel = channel.parent
        if isinstance(
            channel,
            (
                discord.TextChannel,
                discord.VoiceChannel,
                discord.StageChannel,
                discord.ForumChannel,
            ),
        ):
            return channel
        return None

    @staticmethod
    def _is_public_channel(
        guild: discord.Guild,
        channel: InviteChannel,
    ) -> bool:
        default_role = getattr(guild, "default_role", None)
        if default_role is None:
            return True
        return channel.permissions_for(default_role).view_channel

    def _invite_channel(self, ctx: commands.Context) -> InviteChannel | None:
        candidates: list[object] = []
        global_vars = getattr(self.bot, "global_vars", {}) or {}
        get_channel = getattr(ctx.guild, "get_channel", None)
        if callable(get_channel):
            for variable_name in ("RULE_CHANNEL", "JOIN_CHANNEL"):
                channel_id = global_vars.get(variable_name)
                try:
                    configured = get_channel(int(channel_id))
                except (TypeError, ValueError):
                    continue
                if configured is not None:
                    candidates.append(configured)

        system_channel = getattr(ctx.guild, "system_channel", None)
        if system_channel is not None:
            candidates.append(system_channel)
        candidates.append(ctx.channel)

        seen_ids: set[int] = set()
        for candidate in candidates:
            channel = self._as_invite_channel(candidate)
            if channel is None or channel.id in seen_ids:
                continue
            seen_ids.add(channel.id)
            if (
                self._is_public_channel(ctx.guild, channel)
                and self._can_create_invite(ctx.guild, channel, ctx.author)
            ):
                return channel
        return None

    @staticmethod
    def _can_create_invite(
        guild: discord.Guild,
        channel: InviteChannel | None,
        moderator: discord.Member,
    ) -> bool:
        bot_member = guild.me
        if channel is None or bot_member is None:
            return False
        bot_permissions = channel.permissions_for(bot_member)
        moderator_permissions = channel.permissions_for(moderator)
        return (
            bot_permissions.view_channel
            and bot_permissions.create_instant_invite
            and moderator_permissions.view_channel
            and moderator_permissions.create_instant_invite
        )

    @staticmethod
    async def _deliver_reinvite_fallback(
        interaction: discord.Interaction,
        moderator: discord.Member,
        target: discord.abc.User,
        invite: discord.Invite,
    ) -> str:
        private_message = (
            f"Invite dành cho {_safe_text(str(target), max_length=100)} "
            f"(`{target.id}`): {invite.url}\n"
            "Link chỉ dùng 1 lần và hết hạn sau 7 ngày."
        )
        try:
            await interaction.followup.send(
                private_message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return (
                "\nKhông thể gửi DM cho người dùng; "
                "link reinvite đã được gửi riêng cho moderator."
            )
        except discord.HTTPException:
            logger.warning(
                "Could not send ephemeral reinvite target=%s moderator=%s",
                target.id,
                moderator.id,
            )

        try:
            await moderator.send(
                private_message,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return (
                "\nKhông thể gửi DM cho người dùng; "
                "link reinvite đã được gửi qua DM cho moderator."
            )
        except discord.HTTPException:
            logger.warning(
                "Could not DM reinvite to moderator=%s target=%s",
                moderator.id,
                target.id,
            )

        try:
            await invite.delete(
                reason=format_audit_reason(
                    "Reinvite delivery failed; revoking unused invite",
                    moderator,
                )
            )
            return (
                "\nKhông thể gửi link reinvite cho người dùng hoặc moderator; "
                "invite chưa dùng đã được hủy."
            )
        except discord.HTTPException:
            logger.error(
                "Could not revoke undelivered reinvite target=%s moderator=%s",
                target.id,
                moderator.id,
            )
            return (
                "\nKhông thể gửi link reinvite và cũng không thể tự hủy invite. "
                "Hãy kiểm tra danh sách invite của server."
            )

    async def _submit_unban(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: UnbanRequest,
        *,
        invite_channel: InviteChannel | None,
    ) -> UnbanActionResult:
        guild = interaction.guild
        if guild is None:
            return UnbanActionResult(False, "Lệnh unban chỉ dùng được trong server.")

        moderator = interaction.user
        denial = unban_permission_denial(guild, moderator)
        if denial is not None:
            return UnbanActionResult(False, denial)

        try:
            entry = await guild.fetch_ban(discord.Object(id=request.target_id))
        except discord.NotFound:
            return UnbanActionResult(
                True,
                "Người dùng này không còn trong danh sách ban.",
            )
        except discord.Forbidden:
            return UnbanActionResult(
                False,
                "Bot không thể đọc danh sách ban. Hãy kiểm tra quyền Ban Members.",
            )
        except discord.HTTPException:
            logger.exception(
                "Discord rejected fetch_ban target=%s moderator=%s",
                request.target_id,
                moderator.id,
            )
            return UnbanActionResult(
                False,
                "Không thể kiểm tra trạng thái ban lúc này. Vui lòng thử lại.",
            )

        if request.reinvite:
            if entry.user.bot:
                return UnbanActionResult(
                    False,
                    (
                        "Không thể gửi reinvite cho tài khoản bot. "
                        "Hãy mở lại bảng và chọn không tạo invite."
                    ),
                )
            if not self._can_create_invite(guild, invite_channel, moderator):
                return UnbanActionResult(
                    False,
                    (
                        "Bạn và bot đều cần quyền Create Invite trong kênh này. "
                        "Hãy cấp quyền hoặc mở lại bảng và chọn không tạo invite."
                    ),
                )

        reason = clean_case_reason(request.reason)
        try:
            await guild.unban(
                entry.user,
                reason=format_audit_reason(reason, moderator),
            )
        except discord.NotFound:
            return UnbanActionResult(
                True,
                "Người dùng này đã được gỡ ban trước khi thao tác hoàn tất.",
            )
        except discord.Forbidden:
            return UnbanActionResult(
                False,
                "Bot không thể unban người dùng này. Hãy kiểm tra quyền Ban Members.",
            )
        except discord.HTTPException:
            logger.exception(
                "Discord rejected unban target=%s moderator=%s",
                entry.user.id,
                moderator.id,
            )
            return UnbanActionResult(
                False,
                "Discord từ chối thao tác unban. Vui lòng thử lại.",
            )

        case_number = await record_case(
            self.bot,
            guild=guild,
            target=entry.user,
            moderator=moderator,
            action="unban",
            reason=reason,
        )

        base_message = (
            f"Đã gỡ ban {_safe_text(str(entry.user), max_length=100)} "
            f"(`{entry.user.id}`).\n"
            f"Lý do: {_safe_text(reason)}{case_suffix(case_number)}"
        )
        if not request.reinvite:
            return UnbanActionResult(True, base_message)

        try:
            invite = await invite_channel.create_invite(
                max_age=REINVITE_MAX_AGE_SECONDS,
                max_uses=1,
                temporary=False,
                unique=True,
                reason=format_audit_reason(
                    f"One-use reinvite for unban target {entry.user.id}",
                    moderator,
                ),
            )
        except discord.Forbidden:
            return UnbanActionResult(
                True,
                f"{base_message}\nĐã unban nhưng bot không thể tạo invite.",
            )
        except (discord.NotFound, discord.HTTPException):
            logger.exception(
                "Could not create reinvite after unban target=%s channel=%s",
                entry.user.id,
                getattr(invite_channel, "id", "unknown"),
            )
            return UnbanActionResult(
                True,
                f"{base_message}\nĐã unban nhưng không thể tạo invite lúc này.",
            )

        try:
            await entry.user.send(
                (
                    f"Bạn đã được gỡ ban khỏi **{_safe_text(guild.name, max_length=100)}**.\n"
                    f"Lý do: {_safe_text(reason, max_length=1000)}\n"
                    f"Invite dùng 1 lần, hết hạn sau 7 ngày: {invite.url}"
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            reinvite_status = "\nĐã gửi invite dùng 1 lần qua DM."
        except discord.HTTPException:
            logger.info(
                "Could not DM reinvite target=%s guild=%s",
                entry.user.id,
                guild.id,
            )
            reinvite_status = await self._deliver_reinvite_fallback(
                interaction,
                moderator,
                entry.user,
                invite,
            )

        return UnbanActionResult(
            True,
            f"{base_message}{reinvite_status}",
        )

    @commands.command(
        name="unban",
        help="Unban bằng user ID; reply không đối số để mở bảng tùy chọn reinvite.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.cooldown(
        1,
        UNBAN_COMMAND_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def unban_user(
        self,
        ctx: commands.Context,
        target: str | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        denial = unban_permission_denial(ctx.guild, ctx.author)
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        reference = ctx.message.reference
        if reference is not None:
            if target is not None or reason is not None:
                await ctx.reply(
                    (
                        "Khi unban bằng reply, chỉ dùng lệnh "
                        f"`{ctx.clean_prefix}unban` không kèm ID hoặc lý do; "
                        "bạn sẽ chọn lý do trong bảng."
                    ),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            try:
                user_id = await self._resolve_reply_user_id(ctx)
            except UnbanTargetLookupError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        elif target is None:
            await ctx.reply(
                (
                    f"Hãy nhập user ID bằng `{ctx.clean_prefix}unban <user_id>` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}unban`."
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        else:
            try:
                user_id = parse_unban_user_id(target)
            except ValueError:
                await ctx.reply(
                    "User ID không hợp lệ. Hãy dùng ID số hoặc mention người dùng.",
                    mention_author=False,
                )
                return

        if reference is None:
            async def submit_prefix_unban(
                actor: PrefixModerationContext,
                request: UnbanRequest,
            ) -> UnbanActionResult:
                return await self._submit_unban(actor, request, invite_channel=None)

            await run_prefix_action(
                ctx,
                submit_prefix_unban,
                UnbanRequest(user_id, False, clean_case_reason(reason)),
            )
            return

        try:
            entry = await ctx.guild.fetch_ban(discord.Object(id=user_id))
        except discord.NotFound:
            await ctx.reply(
                "Người dùng này không nằm trong danh sách ban.",
                mention_author=False,
            )
            return
        except discord.Forbidden:
            await ctx.reply(
                "Bot không thể đọc danh sách ban. Hãy kiểm tra quyền Ban Members.",
                mention_author=False,
            )
            return
        except discord.HTTPException:
            logger.exception(
                "Could not fetch initial unban target=%s guild=%s",
                user_id,
                ctx.guild.id,
            )
            await ctx.reply(
                "Không thể kiểm tra trạng thái ban lúc này. Hãy thử lại sau.",
                mention_author=False,
            )
            return

        invite_channel = self._invite_channel(ctx)
        reinvite_available = (
            not entry.user.bot
            and invite_channel is not None
        )
        reinvite_destination = (
            f"#{invite_channel.name}"
            if invite_channel is not None
            else None
        )

        async def submit_unban(
            interaction: discord.Interaction,
            request: UnbanRequest,
        ) -> UnbanActionResult:
            return await self._submit_unban(
                interaction,
                request,
                invite_channel=invite_channel,
            )

        view = UnbanWorkflowView(
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=entry.user,
            submitter=submit_unban,
            initial_reason=reason,
            reinvite_available=reinvite_available,
            reinvite_destination=reinvite_destination,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @unban_user.error
    async def unban_user_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Ban Members để unban người dùng.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh unban chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử mở bảng unban lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UnbanCog(bot))
