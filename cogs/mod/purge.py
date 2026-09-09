import logging
from dataclasses import dataclass

import discord
from discord.ext import commands

from cogs.mod._cleanup_state import ACTIVE_CLEANUP_CHANNEL_IDS
from cogs.mod._interaction_ui import (
    ActionResult,
    ConfigurableModerationView,
    IntegerField,
    PrefixModerationContext,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._reply_target import ReplyTargetError, resolve_same_channel_reply_member


logger = logging.getLogger(__name__)

PURGE_MAX_MESSAGES = 1_000
PURGE_USER_SCAN_LIMIT = 5_000
PURGE_COMMAND_COOLDOWN_SECONDS = 5


@dataclass(frozen=True)
class PurgeRequest:
    channel_id: int
    count: int
    target_id: int | None = None


PURGE_SPEC = WorkflowSpec(
    namespace="purge",
    title="Xóa tin nhắn",
    action_text="xóa các tin nhắn đã chọn",
    confirm_label="Có, xóa tin nhắn",
    fields=(
        IntegerField(
            "count",
            "Số tin nhắn",
            minimum=1,
            maximum=PURGE_MAX_MESSAGES,
            placeholder="Ví dụ: 25",
        ),
    ),
    icon="🧹",
)

PURGE_USER_SPEC = WorkflowSpec(
    namespace="purge-user",
    title="Xóa tin nhắn của thành viên",
    action_text="xóa các tin nhắn gần nhất của thành viên",
    confirm_label="Có, xóa tin nhắn",
    fields=(
        IntegerField(
            "count",
            "Số tin nhắn",
            minimum=1,
            maximum=PURGE_MAX_MESSAGES,
            placeholder="Ví dụ: 25",
        ),
    ),
    icon="🧹",
)


def _channel_permission_denial(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    if channel.guild.id != guild.id:
        return "Kênh cần dọn không thuộc server này."
    bot_member = guild.me
    if bot_member is None:
        return "Không thể xác định member của bot trong server."
    if not channel.permissions_for(moderator).manage_messages:
        return "Bạn không còn quyền Manage Messages trong kênh này."
    if not channel.permissions_for(bot_member).manage_messages:
        return "Bot không có quyền Manage Messages trong kênh này."
    return None


def _get_channel(guild: discord.Guild, channel_id: int):
    getter = getattr(guild, "get_channel_or_thread", None)
    return getter(channel_id) if callable(getter) else guild.get_channel(channel_id)


class PruneCommandCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _handle_purge(
        self,
        ctx: commands.Context,
        *,
        target: discord.Member | None,
        initial_count: int | None,
    ) -> None:
        channel = ctx.channel
        denial = _channel_permission_denial(channel, ctx.guild, ctx.author)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return

        target_id = target.id if target is not None else None
        anchor = ctx.message

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            current_channel = _get_channel(guild, channel.id)
            if current_channel is None:
                return "Kênh cần dọn không còn tồn tại."
            return _channel_permission_denial(current_channel, guild, moderator)

        def build_request(answers, _reason) -> PurgeRequest:
            return PurgeRequest(
                channel_id=channel.id,
                count=int(answers["count"].value),
                target_id=target_id,
            )

        async def submit_purge(
            interaction: discord.Interaction | PrefixModerationContext,
            request: PurgeRequest,
        ) -> ActionResult:
            if not 1 <= request.count <= PURGE_MAX_MESSAGES:
                return ActionResult(
                    False,
                    f"Số tin nhắn phải từ 1 đến {PURGE_MAX_MESSAGES:,}.",
                )
            guild = interaction.guild
            if guild is None:
                return ActionResult(False, "Lệnh purge chỉ dùng được trong server.")
            current_channel = _get_channel(guild, request.channel_id)
            if current_channel is None:
                return ActionResult(True, "Kênh cần dọn không còn tồn tại.")
            denial = _channel_permission_denial(
                current_channel,
                guild,
                interaction.user,
            )
            if denial is not None:
                return ActionResult(False, denial)
            if current_channel.id in ACTIVE_CLEANUP_CHANNEL_IDS:
                return ActionResult(
                    False,
                    "Một thao tác dọn tin khác đang chạy trong kênh này.",
                )

            ACTIVE_CLEANUP_CHANNEL_IDS.add(current_channel.id)
            try:
                if request.target_id is None:
                    deleted = await current_channel.purge(
                        limit=request.count,
                        before=anchor,
                    )
                else:
                    remaining = request.count

                    def matches_target(message: discord.Message) -> bool:
                        nonlocal remaining
                        if remaining <= 0 or message.author.id != request.target_id:
                            return False
                        remaining -= 1
                        return True

                    deleted = await current_channel.purge(
                        limit=PURGE_USER_SCAN_LIMIT,
                        before=anchor,
                        check=matches_target,
                    )
            except discord.Forbidden:
                return ActionResult(
                    True,
                    (
                        "Discord báo thiếu quyền sau khi purge đã bắt đầu; "
                        "một số tin nhắn có thể đã được xóa. Thao tác đã dừng để tránh "
                        "xóa lặp. Hãy kiểm tra kênh trước khi mở yêu cầu mới."
                    ),
                )
            except discord.HTTPException:
                logger.exception(
                    "Discord rejected purge channel=%s moderator=%s target=%s",
                    request.channel_id,
                    interaction.user.id,
                    request.target_id,
                )
                return ActionResult(
                    True,
                    (
                        "Discord báo lỗi sau khi purge đã bắt đầu; một số tin nhắn "
                        "có thể đã được xóa. Thao tác đã dừng để tránh xóa lặp. "
                        "Hãy kiểm tra kênh trước khi mở yêu cầu mới."
                    ),
                )
            finally:
                ACTIVE_CLEANUP_CHANNEL_IDS.discard(current_channel.id)

            if request.target_id is None:
                message = f"Đã xóa {len(deleted):,} tin nhắn trong kênh."
            else:
                target_name = (
                    str(target)
                    if target is not None and target.id == request.target_id
                    else "Thành viên"
                )
                message = (
                    f"Đã xóa {len(deleted):,}/{request.count:,} tin nhắn gần nhất "
                    f"của {target_name} (`{request.target_id}`)."
                )
            return ActionResult(True, message)

        if initial_count is not None:
            await run_prefix_action(
                ctx,
                submit_purge,
                PurgeRequest(channel.id, initial_count, target_id),
            )
            return

        view = ConfigurableModerationView(
            spec=PURGE_USER_SPEC if target is not None else PURGE_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=(
                WorkflowTarget(target.id, str(target))
                if target is not None
                else WorkflowTarget(channel.id, f"#{channel.name}")
            ),
            submitter=submit_purge,
            request_builder=build_request,
            live_permission_check=live_permission_check,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(
        name="purge",
        help="Xóa ngay khi có số lượng; bỏ trống để mở bảng xác nhận.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(
        1,
        PURGE_COMMAND_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def prune_messages(
        self,
        ctx: commands.Context,
        number: int | None = None,
    ) -> None:
        await self._handle_purge(
            ctx,
            target=None,
            initial_count=number,
        )

    @commands.command(
        name="purge_user",
        help="Xóa ngay khi có member và số lượng; thiếu số lượng để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(
        1,
        PURGE_COMMAND_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def prune_user_messages(
        self,
        ctx: commands.Context,
        user: discord.Member | None = None,
        number: int | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if user is not None or number is not None:
                await ctx.reply(
                    (
                        "Khi purge bằng reply, chỉ dùng "
                        f"`{ctx.clean_prefix}purge_user`; nhập số lượng trong bảng."
                    ),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            try:
                user = await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        elif user is None:
            await ctx.reply(
                (
                    f"Hãy mention member bằng `{ctx.clean_prefix}purge_user @user` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}purge_user`."
                ),
                mention_author=False,
            )
            return

        await self._handle_purge(
            ctx,
            target=user,
            initial_count=number,
        )

    @prune_messages.error
    @prune_user_messages.error
    async def purge_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Manage Messages trong kênh này.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy thành viên cần dọn tin.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Thành viên hoặc số lượng tin nhắn không hợp lệ.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử purge lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PruneCommandCog(bot))
