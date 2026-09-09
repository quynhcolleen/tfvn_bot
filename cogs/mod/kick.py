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
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._reply_target import (
    ReplyTargetError,
    resolve_same_channel_reply_member,
)


logger = logging.getLogger(__name__)
KICK_COMMAND_COOLDOWN_SECONDS = 5

KICK_WORKFLOW_SPEC = WorkflowSpec(
    namespace="kick",
    title="Kick thành viên",
    action_text="kick thành viên",
    confirm_label="Có, kick thành viên",
    reason=COMMON_REASON_CONFIG,
    icon="🥾",
    confirm_color=0xED4245,
)


@dataclass(frozen=True)
class KickRequest:
    target_id: int
    reason: str


def kick_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "kick_members", False):
        return "Bạn không còn quyền Kick Members."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "kick_members", False):
        return "Bot không có quyền Kick Members."
    return None


def kick_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    denial = kick_permission_denial(guild, moderator)
    if denial is not None:
        return denial
    if target.guild.id != guild.id:
        return "Thành viên cần kick không thuộc server này."
    if not can_moderate(moderator, target):
        return "Bạn không thể kick chính mình, server owner, hoặc role ngang/cao hơn."
    bot_member = guild.me
    if target.id == bot_member.id:
        return "Bot không thể tự kick chính mình."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên cần kick."
    return None


class KickCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active_targets: set[tuple[int, int]] = set()

    async def _submit_kick(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: KickRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh kick chỉ dùng được trong server.")
        key = (guild.id, request.target_id)
        if key in self._active_targets:
            return ActionResult(False, "Một thao tác kick cho thành viên này đang chạy.")
        self._active_targets.add(key)
        try:
            try:
                target = await guild.fetch_member(request.target_id)
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần kick không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể tải lại thông tin thành viên.")
            except discord.HTTPException:
                logger.exception("Could not refresh kick target=%s", request.target_id)
                return ActionResult(False, "Không thể kiểm tra thành viên lúc này.")

            moderator = interaction.user
            denial = kick_target_denial(guild, moderator, target)
            if denial is not None:
                return ActionResult(False, denial)
            reason = clean_case_reason(request.reason)
            try:
                await target.kick(reason=format_audit_reason(reason, moderator))
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần kick không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(
                    False,
                    "Bot không thể kick thành viên này. Hãy kiểm tra quyền và thứ bậc role.",
                )
            except discord.HTTPException:
                logger.exception("Discord rejected kick target=%s", target.id)
                return ActionResult(False, "Discord từ chối thao tác kick. Vui lòng thử lại.")

            case_number = await record_case(
                self.bot,
                guild=guild,
                target=target,
                moderator=moderator,
                action="kick",
                reason=reason,
            )
            return ActionResult(
                True,
                f"Đã kick {target} (`{target.id}`). Lý do: {reason}{case_suffix(case_number)}",
            )
        finally:
            self._active_targets.discard(key)

    @commands.command(
        name="kick",
        help="Kick member trực tiếp; reply không kèm đối số để mở bảng kick.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(kick_members=True)
    @commands.cooldown(1, KICK_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def kick_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if member is not None or reason is not None:
                await ctx.reply(
                    f"Khi kick bằng reply, chỉ dùng `{ctx.clean_prefix}kick` không kèm đối số.",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            try:
                member = await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        elif member is None:
            await ctx.reply(
                f"Hãy mention member hoặc reply tin nhắn bằng `{ctx.clean_prefix}kick`.",
                mention_author=False,
            )
            return

        if ctx.message.reference is None:
            await run_prefix_action(
                ctx,
                self._submit_kick,
                KickRequest(member.id, clean_case_reason(reason)),
            )
            return

        denial = kick_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return

        async def submitter(
            interaction: discord.Interaction,
            request: KickRequest,
        ) -> ActionResult:
            return await self._submit_kick(interaction, request)

        view = ConfigurableModerationView(
            spec=KICK_WORKFLOW_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=submitter,
            request_builder=lambda _values, selected_reason: KickRequest(
                member.id,
                clean_case_reason(selected_reason),
            ),
            live_permission_check=kick_permission_denial,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @kick_member.error
    async def kick_member_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền kick thành viên.", mention_author=False)
            return
        if isinstance(error, (commands.MemberNotFound, commands.BadArgument)):
            await ctx.reply("Không tìm thấy thành viên cần kick.", mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử kick lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KickCog(bot))
