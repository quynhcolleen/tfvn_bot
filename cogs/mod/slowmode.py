import logging
from dataclasses import dataclass

import discord
from discord.ext import commands

from cogs.mod._case_helpers import can_moderate, clean_case_reason, format_audit_reason
from cogs.mod._interaction_ui import (
    ActionResult,
    COMMON_REASON_CONFIG,
    ConfigurableModerationView,
    PrefixModerationContext,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)
from cogs.mod._reply_target import ReplyTargetError, resolve_same_channel_reply_member


logger = logging.getLogger(__name__)

SLOWMODE_ACTION_COOLDOWN_SECONDS = 5

_TEXT_CHANNEL_TYPES = {
    discord.ChannelType.text,
    discord.ChannelType.news,
}
_THREAD_CHANNEL_TYPES = {
    discord.ChannelType.news_thread,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
}


@dataclass(frozen=True)
class SlowmodeOverrideRequest:
    channel_id: int
    target_id: int
    immune: bool
    reason: str


SLOWMODE_IMMUNE_SPEC = WorkflowSpec(
    namespace="slowmode-immune",
    title="Cấp miễn slowmode",
    action_text="cấp miễn slowmode trong kênh này",
    confirm_label="Có, cấp miễn",
    reason=COMMON_REASON_CONFIG,
    icon="🐇",
    confirm_style=discord.ButtonStyle.success,
    confirm_color=0x57F287,
)

SLOWMODE_PROMINENT_SPEC = WorkflowSpec(
    namespace="slowmode-prominent",
    title="Gỡ miễn slowmode",
    action_text="gỡ miễn slowmode trong kênh này",
    confirm_label="Có, gỡ miễn",
    reason=COMMON_REASON_CONFIG,
    icon="🐢",
)


def slowmode_override_denial(
    channel: discord.abc.GuildChannel | discord.Thread,
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    normalized_channel = _normalize_slowmode_override_channel(channel)
    if normalized_channel is None:
        return (
            "Chỉ có thể thay đổi miễn slowmode trong kênh text "
            "hoặc thread thuộc kênh text."
        )
    channel = normalized_channel
    if target.guild.id != guild.id:
        return "Thành viên không thuộc server này."
    permissions = channel.permissions_for(moderator)
    if not permissions.manage_roles:
        return "Bạn không còn quyền Manage Roles trong kênh này."
    if not can_moderate(moderator, target):
        return "Bạn không thể thay đổi overwrite của self, owner, hoặc role ngang/cao hơn."

    bot_member = guild.me
    if bot_member is None:
        return "Không thể xác định member của bot trong server."
    bot_permissions = channel.permissions_for(bot_member)
    if not bot_permissions.manage_roles:
        return "Bot không có quyền Manage Roles trong kênh này."
    if target.id == bot_member.id:
        return "Bot không thể tự thay đổi overwrite bằng lệnh này."
    if bot_member.id != guild.owner_id and bot_member.top_role <= target.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên."
    return None


def _get_channel(guild: discord.Guild, channel_id: int):
    getter = getattr(guild, "get_channel_or_thread", None)
    return getter(channel_id) if callable(getter) else guild.get_channel(channel_id)


def _normalize_slowmode_override_channel(
    channel: discord.abc.GuildChannel | discord.Thread,
) -> discord.TextChannel | None:
    """Resolve thread overwrites to a text parent and reject other channels."""
    channel_type = getattr(channel, "type", None)
    if isinstance(channel, discord.Thread) or channel_type in _THREAD_CHANNEL_TYPES:
        parent = getattr(channel, "parent", None)
        if parent is None:
            return None
        parent_type = getattr(parent, "type", None)
        if isinstance(parent, discord.TextChannel) or parent_type in _TEXT_CHANNEL_TYPES:
            return parent
        return None
    if isinstance(channel, discord.TextChannel) or channel_type in _TEXT_CHANNEL_TYPES:
        return channel
    return None


class SlowmodeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._active_targets: set[tuple[int, int, int]] = set()

    @commands.group(name="slowmode", invoke_without_command=True)
    @commands.guild_only()
    async def slowmode(self, ctx: commands.Context) -> None:
        await ctx.send(
            "**Công cụ slowmode**\n"
            f"`{ctx.clean_prefix}slowmode check_bypass [@user]` — kiểm tra bypass.\n"
            f"`{ctx.clean_prefix}slowmode immune @user` — cấp bypass trong kênh.\n"
            f"`{ctx.clean_prefix}slowmode prominent @user` — gỡ bypass trong kênh.\n"
            f"Danh mục đầy đủ: `{ctx.clean_prefix}help moderation`."
        )

    @slowmode.command(name="check_bypass")
    @commands.guild_only()
    async def check_slowmode_bypass(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if member is not None:
                await ctx.reply(
                    "Khi kiểm tra bằng reply, không nhập thêm member.",
                    mention_author=False,
                )
                return
            try:
                member = await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(str(exc), mention_author=False)
                return
        target = member or ctx.author
        perms = ctx.channel.permissions_for(target)
        if perms.bypass_slowmode:
            message = f"{target.mention} có thể bypass chế độ chậm trong kênh này."
        else:
            message = f"{target.mention} không thể bypass chế độ chậm trong kênh này."
        await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())

    @check_slowmode_bypass.error
    async def check_slowmode_bypass_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send("Thành viên không hợp lệ.")
            return
        raise error

    async def _open_override_view(
        self,
        ctx: commands.Context,
        *,
        member: discord.Member,
        immune: bool,
        initial_reason: str | None,
        direct: bool = False,
    ) -> None:
        channel = _normalize_slowmode_override_channel(ctx.channel)
        if channel is None:
            await ctx.reply(
                (
                    "Chỉ có thể thay đổi miễn slowmode trong kênh text "
                    "hoặc thread thuộc kênh text."
                ),
                mention_author=False,
            )
            return
        denial = slowmode_override_denial(
            channel,
            ctx.guild,
            ctx.author,
            member,
        )
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            stored_channel = _get_channel(guild, channel.id)
            target = guild.get_member(member.id)
            if stored_channel is None:
                return "Kênh này không còn tồn tại."
            current_channel = _normalize_slowmode_override_channel(stored_channel)
            if current_channel is None:
                return "Kênh này không hỗ trợ permission overwrite cho slowmode."
            if target is None:
                return "Thành viên không còn ở trong server."
            return slowmode_override_denial(
                current_channel,
                guild,
                moderator,
                target,
            )

        def build_request(_answers, reason) -> SlowmodeOverrideRequest:
            return SlowmodeOverrideRequest(
                channel_id=channel.id,
                target_id=member.id,
                immune=immune,
                reason=clean_case_reason(reason),
            )

        async def submit_override(
            interaction: discord.Interaction | PrefixModerationContext,
            request: SlowmodeOverrideRequest,
        ) -> ActionResult:
            guild = interaction.guild
            if guild is None:
                return ActionResult(False, "Lệnh slowmode chỉ dùng được trong server.")
            stored_channel = _get_channel(guild, request.channel_id)
            target = guild.get_member(request.target_id)
            if stored_channel is None:
                return ActionResult(True, "Kênh này không còn tồn tại.")
            current_channel = _normalize_slowmode_override_channel(stored_channel)
            if current_channel is None:
                return ActionResult(
                    True,
                    "Kênh này không hỗ trợ permission overwrite cho slowmode.",
                )
            if target is None:
                return ActionResult(True, "Thành viên không còn ở trong server.")
            denial = slowmode_override_denial(
                current_channel,
                guild,
                interaction.user,
                target,
            )
            if denial is not None:
                return ActionResult(False, denial)

            lock_key = (guild.id, current_channel.id, target.id)
            if lock_key in self._active_targets:
                return ActionResult(
                    False,
                    "Một thao tác slowmode khác đang xử lý thành viên này.",
                )

            overwrite = current_channel.overwrites_for(target)
            current_value = overwrite.bypass_slowmode
            if request.immune and current_value is True:
                return ActionResult(
                    True,
                    f"{target} (`{target.id}`) đã được miễn slowmode trong kênh này.",
                )
            if not request.immune and current_value is not True:
                return ActionResult(
                    True,
                    f"{target} (`{target.id}`) hiện không có miễn slowmode riêng.",
                )

            overwrite.bypass_slowmode = True if request.immune else None
            reason = clean_case_reason(request.reason)
            self._active_targets.add(lock_key)
            try:
                await current_channel.set_permissions(
                    target,
                    overwrite=None if overwrite.is_empty() else overwrite,
                    reason=format_audit_reason(reason, interaction.user),
                )
            except discord.Forbidden:
                return ActionResult(
                    False,
                    "Bot không thể cập nhật permission overwrite trong kênh này.",
                )
            except discord.HTTPException:
                logger.exception(
                    "Discord rejected slowmode override channel=%s target=%s moderator=%s",
                    current_channel.id,
                    target.id,
                    interaction.user.id,
                )
                return ActionResult(
                    False,
                    "Discord từ chối cập nhật overwrite. Vui lòng thử lại.",
                )
            finally:
                self._active_targets.discard(lock_key)

            action = "cấp" if request.immune else "gỡ"
            return ActionResult(
                True,
                (
                    f"Đã {action} miễn slowmode cho {target} (`{target.id}`) "
                    f"trong #{current_channel.name}.\nLý do: {reason}"
                ),
            )

        if direct:
            await run_prefix_action(
                ctx,
                submit_override,
                build_request({}, initial_reason),
            )
            return

        view = ConfigurableModerationView(
            spec=SLOWMODE_IMMUNE_SPEC if immune else SLOWMODE_PROMINENT_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=submit_override,
            request_builder=build_request,
            live_permission_check=live_permission_check,
            initial_reason=initial_reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _resolve_override_target(
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
                    (
                        "Khi dùng bằng reply, chỉ nhập "
                        f"`{ctx.clean_prefix}slowmode {command_name}`; "
                        "chọn lý do trong bảng."
                    ),
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
                (
                    f"Hãy mention member hoặc reply tin nhắn bằng "
                    f"`{ctx.clean_prefix}slowmode {command_name}`."
                ),
                mention_author=False,
            )
            return None
        return member

    @slowmode.command(
        name="immune",
        help="Cấp miễn slowmode cho member; reply không tham số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.cooldown(
        1,
        SLOWMODE_ACTION_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def slowmode_immune(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        target = await self._resolve_override_target(
            ctx,
            member,
            reason,
            command_name="immune",
        )
        if target is not None:
            await self._open_override_view(
                ctx,
                member=target,
                immune=True,
                initial_reason=reason,
                direct=member is not None,
            )

    @slowmode.command(
        name="prominent",
        help="Gỡ miễn slowmode cho member; reply không tham số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)
    @commands.cooldown(
        1,
        SLOWMODE_ACTION_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def slowmode_prominent(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        target = await self._resolve_override_target(
            ctx,
            member,
            reason,
            command_name="prominent",
        )
        if target is not None:
            await self._open_override_view(
                ctx,
                member=target,
                immune=False,
                initial_reason=reason,
                direct=member is not None,
            )

    @slowmode_immune.error
    @slowmode_prominent.error
    async def slowmode_action_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn cần quyền Manage Roles trong kênh này.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply("Không tìm thấy thành viên.", mention_author=False)
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply("Thành viên không hợp lệ.", mention_author=False)
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlowmodeCog(bot))
