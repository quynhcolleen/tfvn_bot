import logging
from dataclasses import dataclass
from datetime import timedelta

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


logger = logging.getLogger(__name__)

MAX_CLEAN_BEFORE_DAYS = 3_650
CLEAN_BEFORE_COOLDOWN_SECONDS = 10


@dataclass(frozen=True)
class CleanBeforeRequest:
    channel_id: int
    days: int


CLEAN_BEFORE_SPEC = WorkflowSpec(
    namespace="clean-before",
    title="Dọn tin nhắn cũ",
    action_text="xóa toàn bộ tin nhắn cũ hơn mốc đã chọn",
    confirm_label="Có, dọn tin cũ",
    fields=(
        IntegerField(
            "days",
            "Số ngày",
            minimum=1,
            maximum=MAX_CLEAN_BEFORE_DAYS,
            placeholder="Ví dụ: 30",
        ),
    ),
    icon="🧹",
)


def _cleanup_permission_denial(
    channel: discord.abc.GuildChannel,
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
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


class JanitorCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name="clean_before",
        help="Dọn ngay khi có số ngày; bỏ trống để mở bảng xác nhận.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(
        1,
        CLEAN_BEFORE_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def clean_messages_created_before(
        self,
        ctx: commands.Context,
        days: int | None = None,
    ) -> None:
        channel = ctx.channel
        denial = _cleanup_permission_denial(channel, ctx.guild, ctx.author)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return

        anchor = ctx.message
        reference_time = discord.utils.utcnow()

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            current_channel = _get_channel(guild, channel.id)
            if current_channel is None:
                return "Kênh cần dọn không còn tồn tại."
            return _cleanup_permission_denial(
                current_channel,
                guild,
                moderator,
            )

        def build_request(answers, _reason) -> CleanBeforeRequest:
            return CleanBeforeRequest(
                channel_id=channel.id,
                days=int(answers["days"].value),
            )

        async def submit_cleanup(
            interaction: discord.Interaction | PrefixModerationContext,
            request: CleanBeforeRequest,
        ) -> ActionResult:
            if not 1 <= request.days <= MAX_CLEAN_BEFORE_DAYS:
                return ActionResult(
                    False,
                    f"Số ngày phải từ 1 đến {MAX_CLEAN_BEFORE_DAYS:,}.",
                )
            guild = interaction.guild
            if guild is None:
                return ActionResult(
                    False,
                    "Lệnh clean_before chỉ dùng được trong server.",
                )
            current_channel = _get_channel(guild, request.channel_id)
            if current_channel is None:
                return ActionResult(True, "Kênh cần dọn không còn tồn tại.")
            denial = _cleanup_permission_denial(
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

            cutoff = reference_time - timedelta(days=request.days)
            ACTIVE_CLEANUP_CHANNEL_IDS.add(current_channel.id)
            try:
                deleted = await current_channel.purge(
                    limit=None,
                    before=anchor,
                    check=lambda message: message.created_at < cutoff,
                )
            except discord.Forbidden:
                return ActionResult(
                    False,
                    "Bot không thể xóa tin nhắn trong kênh này.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Discord rejected clean_before channel=%s moderator=%s days=%s",
                    request.channel_id,
                    interaction.user.id,
                    request.days,
                )
                return ActionResult(
                    False,
                    "Discord từ chối thao tác dọn tin. Vui lòng thử lại.",
                )
            finally:
                ACTIVE_CLEANUP_CHANNEL_IDS.discard(current_channel.id)

            return ActionResult(
                True,
                (
                    f"Đã xóa {len(deleted):,} tin nhắn cũ hơn "
                    f"{request.days:,} ngày trong #{current_channel.name}."
                ),
            )

        if days is not None:
            await run_prefix_action(
                ctx,
                submit_cleanup,
                CleanBeforeRequest(channel.id, days),
            )
            return

        view = ConfigurableModerationView(
            spec=CLEAN_BEFORE_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(channel.id, f"#{channel.name}"),
            submitter=submit_cleanup,
            request_builder=build_request,
            live_permission_check=live_permission_check,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @clean_messages_created_before.error
    async def clean_before_error(
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
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Số ngày không hợp lệ; hãy nhập số nguyên dương.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử dọn tin lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JanitorCog(bot))
