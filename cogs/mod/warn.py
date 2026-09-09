from dataclasses import dataclass
from datetime import timezone

import discord
from discord.ext import commands
from pymongo import DESCENDING

from cogs.mod._case_helpers import can_moderate, case_suffix, clean_case_reason, record_case
from cogs.mod._interaction_ui import (
    COMMON_REASON_CONFIG,
    ActionResult,
    ConfigurableModerationView,
    PrefixModerationContext,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._reply_target import ReplyTargetError, resolve_same_channel_reply_member


WARN_COMMAND_COOLDOWN_SECONDS = 5
WARN_WORKFLOW_SPEC = WorkflowSpec(
    namespace="warn",
    title="Cảnh cáo thành viên",
    action_text="cảnh cáo thành viên",
    confirm_label="Có, lưu cảnh cáo",
    reason=COMMON_REASON_CONFIG,
    icon="⚠️",
    confirm_color=0xED4245,
)


@dataclass(frozen=True)
class WarnRequest:
    target_id: int
    reason: str


def warn_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_messages", False):
        return "Bạn không còn quyền Manage Messages để cảnh cáo thành viên."
    return None


def warn_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    denial = warn_permission_denial(guild, moderator)
    if denial is not None:
        return denial
    if target.guild.id != guild.id:
        return "Thành viên cần cảnh cáo không thuộc server này."
    if not can_moderate(moderator, target):
        return "Bạn không thể cảnh cáo chính mình, server owner, hoặc role ngang/cao hơn."
    bot_member = guild.me
    if bot_member is None:
        return "Bot không còn là thành viên của server."
    if target.id == bot_member.id:
        return "Bot không thể tự cảnh cáo chính mình."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên cần cảnh cáo."
    return None


class WarnCommandCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self._active_targets: set[tuple[int, int]] = set()

    async def _submit_warn(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: WarnRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh warn chỉ dùng được trong server.")
        key = (guild.id, request.target_id)
        if key in self._active_targets:
            return ActionResult(False, "Một cảnh cáo cho thành viên này đang được lưu.")
        self._active_targets.add(key)
        try:
            try:
                target = await guild.fetch_member(request.target_id)
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần cảnh cáo không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể tải lại thông tin thành viên.")
            except discord.HTTPException:
                return ActionResult(False, "Không thể kiểm tra thành viên lúc này.")

            moderator = interaction.user
            denial = warn_target_denial(guild, moderator, target)
            if denial is not None:
                return ActionResult(False, denial)
            reason = clean_case_reason(request.reason)
            now = discord.utils.utcnow()
            self.db["warnings"].insert_one(
                {
                    "guild_id": guild.id,
                    "user_id": target.id,
                    "user_name": str(target),
                    "moderator_id": moderator.id,
                    "moderator_name": str(moderator),
                    "reason": reason,
                    "timestamp": now,
                }
            )
            case_number = await record_case(
                self.bot,
                guild=guild,
                target=target,
                moderator=moderator,
                action="warn",
                reason=reason,
            )
            return ActionResult(
                True,
                f"Đã cảnh cáo {target} (`{target.id}`). Lý do: {reason}{case_suffix(case_number)}",
            )
        finally:
            self._active_targets.discard(key)

    @commands.command(
        name="warn",
        help="Cảnh cáo member trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    @commands.cooldown(1, WARN_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def warn_user(
        self,
        ctx: commands.Context,
        user: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if user is not None or reason is not None:
                await ctx.reply(
                    f"Khi warn bằng reply, chỉ dùng `{ctx.clean_prefix}warn` không kèm đối số.",
                    mention_author=False,
                )
                return
            try:
                user = await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(str(exc), mention_author=False)
                return
        elif user is None:
            await ctx.reply(
                f"Hãy mention member hoặc reply tin nhắn bằng `{ctx.clean_prefix}warn`.",
                mention_author=False,
            )
            return

        if ctx.message.reference is None:
            await run_prefix_action(
                ctx,
                self._submit_warn,
                WarnRequest(user.id, clean_case_reason(reason)),
            )
            return

        denial = warn_target_denial(ctx.guild, ctx.author, user)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return
        view = ConfigurableModerationView(
            spec=WARN_WORKFLOW_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(user.id, str(user)),
            submitter=self._submit_warn,
            request_builder=lambda _values, selected_reason: WarnRequest(
                user.id,
                clean_case_reason(selected_reason),
            ),
            live_permission_check=warn_permission_denial,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @warn_user.error
    async def warn_user_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền cảnh cáo thành viên.", mention_author=False)
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            await ctx.reply("Không tìm thấy thành viên cần cảnh cáo.", mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử warn lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error

    @commands.command(name="check_warn", help="Xem cảnh cáo gần đây.")
    @commands.guild_only()
    async def check_warnings(
        self,
        ctx: commands.Context,
        user: discord.Member | None = None,
    ) -> None:
        target = user or ctx.author
        warnings = list(
            self.db["warnings"]
            .find(
                {
                    "user_id": target.id,
                    "$or": [
                        {"guild_id": ctx.guild.id},
                        {"guild_id": {"$exists": False}},
                    ],
                }
            )
            .sort("timestamp", DESCENDING)
            .limit(10)
        )

        embed = discord.Embed(title="Lịch sử cảnh cáo", color=discord.Color.blue())
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        if not warnings:
            embed.description = "Người dùng này chưa bị cảnh cáo."
        else:
            for warning in warnings:
                timestamp = warning.get("timestamp")
                if timestamp and timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                when = (
                    discord.utils.format_dt(timestamp, style="f")
                    if timestamp
                    else "Không xác định"
                )
                embed.add_field(
                    name=when,
                    value=(
                        f"Lý do: {warning.get('reason', 'Không có lý do')}\n"
                        f"Mod: {warning.get('moderator_name', 'Không xác định')}"
                    )[:1024],
                    inline=False,
                )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarnCommandCog(bot))
