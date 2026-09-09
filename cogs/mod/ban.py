import logging

import discord
from discord.ext import commands

from cogs.mod._ban_ui import (
    BanActionResult,
    BanRequest,
    BanWorkflowView,
    ban_target_denial,
    format_delete_message_window,
)
from cogs.mod._case_helpers import (
    case_suffix,
    clean_case_reason,
    format_audit_reason,
    record_case,
)
from cogs.mod._interaction_ui import PrefixModerationContext, run_prefix_action


logger = logging.getLogger(__name__)
BAN_COMMAND_COOLDOWN_SECONDS = 5


class BanTargetLookupError(ValueError):
    """Raised when a reply cannot identify a bannable Discord user."""


def _safe_text(value: str, *, max_length: int = 1200) -> str:
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(value))
    if len(escaped) <= max_length:
        return escaped
    return f"{escaped[: max_length - 1]}…"


class BanCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    async def _fetch_replied_message(
        ctx: commands.Context,
        reference: discord.MessageReference,
    ) -> discord.Message:
        if reference.message_id is None:
            raise BanTargetLookupError("Tin nhắn được reply không còn khả dụng.")
        if reference.channel_id != ctx.channel.id:
            raise BanTargetLookupError(
                "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
            )
        try:
            return await ctx.channel.fetch_message(reference.message_id)
        except discord.NotFound as exc:
            raise BanTargetLookupError(
                "Không tìm thấy tin nhắn được reply. Tin nhắn có thể đã bị xóa."
            ) from exc
        except discord.Forbidden as exc:
            raise BanTargetLookupError(
                "Bot không có quyền đọc lịch sử tin nhắn trong kênh này."
            ) from exc
        except discord.HTTPException as exc:
            logger.exception(
                "Could not fetch replied message for ban message=%s",
                reference.message_id,
            )
            raise BanTargetLookupError(
                "Không thể tải tin nhắn được reply lúc này. Hãy thử lại sau."
            ) from exc

    async def _resolve_reply_target(
        self,
        ctx: commands.Context,
    ) -> discord.abc.User:
        reference = ctx.message.reference
        if reference is None:
            raise BanTargetLookupError(
                f"Hãy mention thành viên hoặc reply tin nhắn bằng "
                f"`{ctx.clean_prefix}ban`."
            )

        resolved = reference.resolved
        if isinstance(resolved, discord.DeletedReferencedMessage):
            raise BanTargetLookupError(
                "Tin nhắn được reply đã bị xóa nên không thể xác định thành viên."
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
            raise BanTargetLookupError(
                "Không thể chọn thành viên từ tin nhắn ở server khác."
            )
        if message.channel.id != ctx.channel.id:
            raise BanTargetLookupError(
                "Chỉ có thể chọn mục tiêu từ tin nhắn được reply trong kênh hiện tại."
            )
        if getattr(message, "webhook_id", None) is not None:
            raise BanTargetLookupError(
                "Không thể ban tác giả của tin nhắn webhook."
            )

        target = ctx.guild.get_member(message.author.id)
        if target is None:
            try:
                target = await ctx.guild.fetch_member(message.author.id)
            except discord.NotFound:
                target = message.author
            except discord.Forbidden as exc:
                raise BanTargetLookupError(
                    "Bot không có quyền tải thông tin tác giả của tin nhắn."
                ) from exc
            except discord.HTTPException as exc:
                logger.exception(
                    "Could not fetch reply author for ban user=%s",
                    message.author.id,
                )
                raise BanTargetLookupError(
                    "Không thể tải thông tin tác giả lúc này. Hãy thử lại sau."
                ) from exc
        return target

    async def _submit_ban(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: BanRequest,
        *,
        fallback_target: discord.abc.User | None = None,
    ) -> BanActionResult:
        guild = interaction.guild
        if guild is None:
            return BanActionResult(False, "Lệnh ban chỉ dùng được trong server.")

        current_target = guild.get_member(request.target_id)
        target = current_target or fallback_target
        if target is None:
            return BanActionResult(
                False,
                "Không còn dữ liệu mục tiêu để thực hiện lệnh ban.",
            )

        moderator = interaction.user
        denial = ban_target_denial(guild, moderator, target)
        if denial is not None:
            return BanActionResult(False, denial)

        reason = clean_case_reason(request.reason)
        try:
            ban_options = {
                "reason": format_audit_reason(reason, moderator),
                "delete_message_seconds": request.delete_message_seconds,
            }
            if current_target is not None:
                await current_target.ban(**ban_options)
            else:
                await guild.ban(target, **ban_options)
        except discord.NotFound:
            return BanActionResult(
                False,
                "Không tìm thấy thành viên để ban. Họ có thể đã rời server.",
            )
        except discord.Forbidden:
            return BanActionResult(
                False,
                "Bot không thể ban thành viên này. Hãy kiểm tra quyền và thứ bậc role.",
            )
        except discord.HTTPException:
            logger.exception(
                "Discord rejected interactive ban target=%s moderator=%s",
                target.id,
                moderator.id,
            )
            return BanActionResult(
                False,
                "Discord từ chối thao tác ban. Vui lòng thử lại.",
            )

        case_number = await record_case(
            self.bot,
            guild=guild,
            target=target,
            moderator=moderator,
            action="ban",
            reason=reason,
        )
        return BanActionResult(
            True,
            (
                f"Đã ban {_safe_text(str(target), max_length=100)} (`{target.id}`).\n"
                f"Xóa tin nhắn: "
                f"{format_delete_message_window(request.delete_message_hours)}.\n"
                f"Lý do: {_safe_text(reason)}{case_suffix(case_number)}"
            ),
        )

    @commands.command(
        name="ban",
        help="Ban member được mention; reply không đối số để mở bảng tùy chọn.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.cooldown(
        1,
        BAN_COMMAND_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def ban_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        reference = ctx.message.reference
        if reference is not None:
            if member is not None or reason is not None:
                await ctx.reply(
                    (
                        "Khi ban bằng reply, chỉ dùng lệnh "
                        f"`{ctx.clean_prefix}ban` không kèm mục tiêu hoặc lý do; "
                        "bạn sẽ chọn lý do trong bảng."
                    ),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            try:
                member = await self._resolve_reply_target(ctx)
            except BanTargetLookupError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        elif member is None:
            await ctx.reply(
                (
                    f"Hãy mention thành viên bằng `{ctx.clean_prefix}ban @user` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}ban`."
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        denial = ban_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        async def submit_ban(
            interaction: discord.Interaction | PrefixModerationContext,
            request: BanRequest,
        ) -> BanActionResult:
            return await self._submit_ban(
                interaction,
                request,
                fallback_target=member,
            )

        if reference is None:
            await run_prefix_action(
                ctx,
                submit_ban,
                BanRequest(member.id, 24, clean_case_reason(reason)),
            )
            return

        view = BanWorkflowView(
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=member,
            submitter=submit_ban,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ban_member.error
    async def ban_member_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Ban Members để ban thành viên.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy thành viên cần ban. Hãy mention member hoặc reply tin nhắn.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Mục tiêu ban không hợp lệ. Hãy mention member hoặc reply tin nhắn.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh ban chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử mở bảng ban lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BanCog(bot))
