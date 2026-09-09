import logging
from dataclasses import dataclass

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
    PrefixModerationContext,
    ReasonConfig,
    ReasonPreset,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS
from cogs.mod._reply_target import ReplyTargetError, resolve_same_channel_reply_member


logger = logging.getLogger(__name__)
MUTE_COMMAND_COOLDOWN_SECONDS = 5

RELEASE_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset("expired", "Đã hết thời hạn", "Đã hoàn thành thời hạn xử lý"),
        ReasonPreset("appeal", "Chấp nhận kháng nghị", "Kháng nghị được chấp nhận"),
        ReasonPreset("mistake", "Xử lý nhầm", "Gỡ hạn chế do xử lý nhầm"),
        ReasonPreset("moderator", "Quyết định của moderator", "Moderator gỡ hạn chế"),
    ),
    select_placeholder="Chọn lý do gỡ mute",
    custom_title="Nhập lý do gỡ mute",
)

MUTE_WORKFLOW_SPEC = WorkflowSpec(
    namespace="mute",
    title="Mute thành viên",
    action_text="mute thành viên",
    confirm_label="Có, mute thành viên",
    reason=COMMON_REASON_CONFIG,
    icon="🔇",
    confirm_color=0xED4245,
)
UNMUTE_WORKFLOW_SPEC = WorkflowSpec(
    namespace="unmute",
    title="Gỡ mute thành viên",
    action_text="gỡ mute thành viên",
    confirm_label="Có, gỡ mute",
    reason=RELEASE_REASON_CONFIG,
    icon="🔊",
    confirm_color=0x57F287,
    confirm_style=discord.ButtonStyle.success,
)


@dataclass(frozen=True)
class MuteRequest:
    target_id: int
    remove: bool
    reason: str


def mute_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_roles", False):
        return "Bạn không còn quyền Manage Roles."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_roles", False):
        return "Bot không có quyền Manage Roles."
    return None


def mute_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    denial = mute_permission_denial(guild, moderator)
    if denial is not None:
        return denial
    if target.guild.id != guild.id:
        return "Thành viên cần xử lý không thuộc server này."
    if not can_moderate(moderator, target):
        return "Bạn không thể xử lý chính mình, server owner, hoặc role ngang/cao hơn."
    bot_member = guild.me
    if target.id == bot_member.id:
        return "Bot không thể tự thay đổi role Muted của chính mình."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên cần xử lý."
    return None


class MuteCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _submit_mute(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: MuteRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh mute chỉ dùng được trong server.")
        key = (guild.id, request.target_id)
        if key in ACTIVE_ROLE_MUTATION_TARGETS:
            return ActionResult(False, "Một thao tác role khác cho thành viên này đang chạy.")
        ACTIVE_ROLE_MUTATION_TARGETS.add(key)
        try:
            try:
                target = await guild.fetch_member(request.target_id)
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần xử lý không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể tải lại thông tin thành viên.")
            except discord.HTTPException:
                logger.exception("Could not refresh mute target=%s", request.target_id)
                return ActionResult(False, "Không thể kiểm tra thành viên lúc này.")

            moderator = interaction.user
            denial = mute_target_denial(guild, moderator, target)
            if denial is not None:
                return ActionResult(False, denial)
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role is None:
                return ActionResult(False, "Role Muted không tồn tại.")
            if mute_role >= guild.me.top_role:
                return ActionResult(False, "Role Muted phải thấp hơn role cao nhất của bot.")
            if moderator.id != guild.owner_id and mute_role >= moderator.top_role:
                return ActionResult(False, "Role Muted phải thấp hơn role cao nhất của bạn.")
            if request.remove and mute_role not in target.roles:
                return ActionResult(True, f"{target} (`{target.id}`) không bị mute.")
            if not request.remove and mute_role in target.roles:
                return ActionResult(True, f"{target} (`{target.id}`) đã bị mute.")

            reason = clean_case_reason(request.reason)
            try:
                method = target.remove_roles if request.remove else target.add_roles
                await method(mute_role, reason=format_audit_reason(reason, moderator))
            except discord.NotFound:
                return ActionResult(True, "Thành viên hoặc role Muted không còn tồn tại.")
            except discord.Forbidden:
                action = "gỡ" if request.remove else "gán"
                return ActionResult(False, f"Bot không có quyền {action} role Muted.")
            except discord.HTTPException:
                logger.exception("Discord rejected mute change target=%s", target.id)
                return ActionResult(False, "Discord từ chối thay đổi role Muted.")

            action = "unmute" if request.remove else "mute"
            case_number = await record_case(
                self.bot,
                guild=guild,
                target=target,
                moderator=moderator,
                action=action,
                reason=reason,
            )
            verb = "Đã unmute" if request.remove else "Đã mute"
            return ActionResult(
                True,
                f"{verb} {target} (`{target.id}`){case_suffix(case_number)}.",
            )
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(key)

    async def _open_workflow(
        self,
        ctx: commands.Context,
        member: discord.Member,
        reason: str | None,
        *,
        remove: bool,
    ) -> None:
        denial = mute_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return

        spec = UNMUTE_WORKFLOW_SPEC if remove else MUTE_WORKFLOW_SPEC
        view = ConfigurableModerationView(
            spec=spec,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=self._submit_mute,
            request_builder=lambda _values, selected_reason: MuteRequest(
                member.id,
                remove,
                clean_case_reason(selected_reason),
            ),
            live_permission_check=mute_permission_denial,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_target(
        self,
        ctx: commands.Context,
        member: discord.Member | None,
        reason: str | None,
        *,
        command_name: str,
    ) -> discord.Member | None:
        if ctx.message.reference is not None:
            if member is not None or reason is not None:
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

    @commands.command(
        name="mute",
        help="Gán role Muted trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.cooldown(1, MUTE_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def mute_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(ctx, member, reason, command_name="mute")
        if member is not None:
            if ctx.message.reference is None:
                await run_prefix_action(
                    ctx,
                    self._submit_mute,
                    MuteRequest(member.id, False, clean_case_reason(reason)),
                )
                return
            await self._open_workflow(ctx, member, reason, remove=False)

    @commands.command(
        name="unmute",
        help="Gỡ role Muted trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.cooldown(1, MUTE_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def unmute_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(ctx, member, reason, command_name="unmute")
        if member is not None:
            if ctx.message.reference is None:
                await run_prefix_action(
                    ctx,
                    self._submit_mute,
                    MuteRequest(
                        member.id,
                        True,
                        clean_case_reason(
                            "Moderator removed mute" if reason is None else reason
                        ),
                    ),
                )
                return
            await self._open_workflow(ctx, member, reason, remove=True)

    @mute_member.error
    @unmute_member.error
    async def mute_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền sử dụng lệnh này.", mention_author=False)
            return
        if isinstance(error, (commands.MemberNotFound, commands.BadArgument)):
            await ctx.reply("Không tìm thấy thành viên.", mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử lệnh lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MuteCog(bot))
