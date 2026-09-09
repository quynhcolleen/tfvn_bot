from datetime import timedelta
from dataclasses import dataclass
import logging

import discord
from discord.ext import commands

from cogs.mod._case_helpers import (
    can_moderate,
    case_suffix,
    clean_case_reason,
    format_audit_reason,
    record_case,
)
from cogs.mod._interaction_ui import (
    COMMON_REASON_CONFIG,
    ActionResult,
    ConfigurableModerationView,
    FormAnswer,
    IntegerField,
    PrefixModerationContext,
    ReasonConfig,
    ReasonPreset,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._reply_target import ReplyTargetError, resolve_same_channel_reply_member


logger = logging.getLogger(__name__)
MAX_TIMEOUT_MINUTES = 28 * 24 * 60
TIMEOUT_COMMAND_COOLDOWN_SECONDS = 5

UNTIMEOUT_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset("expired", "Đã hết thời hạn", "Đã hoàn thành thời hạn timeout"),
        ReasonPreset("appeal", "Chấp nhận kháng nghị", "Kháng nghị được chấp nhận"),
        ReasonPreset("mistake", "Timeout nhầm", "Gỡ timeout do xử lý nhầm"),
        ReasonPreset("moderator", "Quyết định của moderator", "Moderator gỡ timeout"),
    ),
    select_placeholder="Chọn lý do gỡ timeout",
    custom_title="Nhập lý do gỡ timeout",
)

TIMEOUT_WORKFLOW_SPEC = WorkflowSpec(
    namespace="timeout",
    title="Timeout thành viên",
    action_text="timeout thành viên",
    confirm_label="Có, timeout thành viên",
    fields=(
        IntegerField(
            "duration_minutes",
            "Thời gian timeout (phút)",
            minimum=1,
            maximum=MAX_TIMEOUT_MINUTES,
            placeholder="Ví dụ: 60",
        ),
    ),
    reason=COMMON_REASON_CONFIG,
    icon="⏳",
    confirm_color=0xED4245,
)
UNTIMEOUT_WORKFLOW_SPEC = WorkflowSpec(
    namespace="untimeout",
    title="Gỡ timeout thành viên",
    action_text="gỡ timeout thành viên",
    confirm_label="Có, gỡ timeout",
    reason=UNTIMEOUT_REASON_CONFIG,
    icon="✅",
    confirm_color=0x57F287,
    confirm_style=discord.ButtonStyle.success,
)


@dataclass(frozen=True)
class TimeoutRequest:
    target_id: int
    duration_minutes: int | None
    reason: str


def timeout_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "moderate_members", False):
        return "Bạn không còn quyền Moderate Members."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "moderate_members", False):
        return "Bot không có quyền Moderate Members."
    return None


def timeout_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    denial = timeout_permission_denial(guild, moderator)
    if denial is not None:
        return denial
    if target.guild.id != guild.id:
        return "Thành viên cần timeout không thuộc server này."
    if not can_moderate(moderator, target):
        return "Bạn không thể timeout chính mình, server owner, hoặc role ngang/cao hơn."
    bot_member = guild.me
    if target.id == bot_member.id:
        return "Bot không thể tự timeout chính mình."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên cần timeout."
    return None


class TimeoutCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active_targets: set[tuple[int, int]] = set()

    async def _submit_timeout(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: TimeoutRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh timeout chỉ dùng được trong server.")
        key = (guild.id, request.target_id)
        if key in self._active_targets:
            return ActionResult(False, "Một thao tác timeout cho thành viên này đang chạy.")
        self._active_targets.add(key)
        try:
            try:
                target = await guild.fetch_member(request.target_id)
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần xử lý không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể tải lại thông tin thành viên.")
            except discord.HTTPException:
                logger.exception("Could not refresh timeout target=%s", request.target_id)
                return ActionResult(False, "Không thể kiểm tra thành viên lúc này.")

            moderator = interaction.user
            denial = timeout_target_denial(guild, moderator, target)
            if denial is not None:
                return ActionResult(False, denial)
            if request.duration_minutes is not None and not (
                1 <= request.duration_minutes <= MAX_TIMEOUT_MINUTES
            ):
                return ActionResult(
                    False,
                    f"Thời gian timeout phải từ 1 đến {MAX_TIMEOUT_MINUTES:,} phút.",
                )
            reason = clean_case_reason(request.reason)
            until = (
                discord.utils.utcnow() + timedelta(minutes=request.duration_minutes)
                if request.duration_minutes is not None
                else None
            )
            try:
                await target.timeout(until, reason=format_audit_reason(reason, moderator))
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần xử lý không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể thay đổi timeout của thành viên này.")
            except discord.HTTPException:
                logger.exception("Discord rejected timeout target=%s", target.id)
                return ActionResult(False, "Discord từ chối thao tác timeout. Vui lòng thử lại.")

            action = "timeout" if request.duration_minutes is not None else "untimeout"
            case_options = {
                "guild": guild,
                "target": target,
                "moderator": moderator,
                "action": action,
                "reason": reason,
            }
            if request.duration_minutes is not None:
                case_options["duration_seconds"] = request.duration_minutes * 60
            case_number = await record_case(self.bot, **case_options)
            if request.duration_minutes is None:
                message = f"Đã gỡ timeout cho {target} (`{target.id}`)"
            else:
                message = (
                    f"Đã timeout {target} (`{target.id}`) trong "
                    f"{request.duration_minutes:,} phút. Lý do: {reason}"
                )
            return ActionResult(True, f"{message}{case_suffix(case_number)}.")
        finally:
            self._active_targets.discard(key)

    async def _resolve_target(
        self,
        ctx: commands.Context,
        member: discord.Member | None,
        duration_minutes: int | None,
        reason: str | None,
        *,
        command_name: str,
    ) -> discord.Member | None:
        if ctx.message.reference is not None:
            if member is not None or duration_minutes is not None or reason is not None:
                await ctx.reply(
                    f"Khi dùng reply, chỉ gọi `{ctx.clean_prefix}{command_name}` không kèm đối số.",
                    mention_author=False,
                )
                return None
            try:
                return await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(str(exc), mention_author=False)
                return None
        if member is None:
            await ctx.reply(
                f"Hãy mention member hoặc reply tin nhắn bằng `{ctx.clean_prefix}{command_name}`.",
                mention_author=False,
            )
            return None
        return member

    async def _open_workflow(
        self,
        ctx: commands.Context,
        member: discord.Member,
        reason: str | None,
        *,
        duration_minutes: int | None,
        remove: bool,
    ) -> None:
        denial = timeout_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return
        if duration_minutes is not None and not 1 <= duration_minutes <= MAX_TIMEOUT_MINUTES:
            await ctx.reply(
                f"Thời gian timeout phải từ 1 đến {MAX_TIMEOUT_MINUTES:,} phút.",
                mention_author=False,
            )
            return

        spec = UNTIMEOUT_WORKFLOW_SPEC if remove else TIMEOUT_WORKFLOW_SPEC
        initial_answers = (
            None
            if remove or duration_minutes is None
            else {
                "duration_minutes": FormAnswer(
                    duration_minutes,
                    f"{duration_minutes:,} phút",
                )
            }
        )
        view = ConfigurableModerationView(
            spec=spec,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=self._submit_timeout,
            request_builder=(
                (lambda _values, selected_reason: TimeoutRequest(
                    member.id,
                    None,
                    clean_case_reason(selected_reason),
                ))
                if remove
                else (lambda values, selected_reason: TimeoutRequest(
                    member.id,
                    int(values["duration_minutes"].value),
                    clean_case_reason(selected_reason),
                ))
            ),
            live_permission_check=timeout_permission_denial,
            initial_reason=reason,
            initial_answers=initial_answers,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(
        name="timeout",
        help="Timeout trực tiếp khi có member và số phút; thiếu số phút để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.cooldown(1, TIMEOUT_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def timeout(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        duration_minutes: int | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(
            ctx,
            member,
            duration_minutes,
            reason,
            command_name="timeout",
        )
        if member is not None:
            if ctx.message.reference is None and duration_minutes is not None:
                await run_prefix_action(
                    ctx,
                    self._submit_timeout,
                    TimeoutRequest(member.id, duration_minutes, clean_case_reason(reason)),
                )
                return
            await self._open_workflow(
                ctx,
                member,
                reason,
                duration_minutes=duration_minutes,
                remove=False,
            )

    @commands.command(
        name="untimeout",
        help="Gỡ timeout trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(moderate_members=True)
    @commands.cooldown(1, TIMEOUT_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def untimeout(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(
            ctx,
            member,
            None,
            reason,
            command_name="untimeout",
        )
        if member is not None:
            if ctx.message.reference is None:
                await run_prefix_action(
                    ctx,
                    self._submit_timeout,
                    TimeoutRequest(
                        member.id,
                        None,
                        clean_case_reason(
                            "Moderator removed timeout" if reason is None else reason
                        ),
                    ),
                )
                return
            await self._open_workflow(
                ctx,
                member,
                reason,
                duration_minutes=None,
                remove=True,
            )

    @timeout.error
    @untimeout.error
    async def timeout_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền timeout thành viên.", mention_author=False)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply("Thành viên hoặc thời gian timeout không hợp lệ.", mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử timeout lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimeoutCog(bot))
