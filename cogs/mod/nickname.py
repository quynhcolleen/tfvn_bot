import logging
from dataclasses import dataclass

import discord
from discord.ext import commands

from cogs.mod._case_helpers import clean_case_reason, format_audit_reason
from cogs.mod._interaction_ui import (
    ActionResult,
    ConfigurableModerationView,
    FormAnswer,
    ModalField,
    ModalInput,
    PrefixModerationContext,
    ReasonConfig,
    ReasonPreset,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
    safe_ui_text,
)
from cogs.mod._reply_target import (
    ReplyTargetError,
    resolve_same_channel_reply_member,
)


logger = logging.getLogger(__name__)

MAX_NICKNAME_LENGTH = 32
NICKNAME_COMMAND_COOLDOWN_SECONDS = 5

NICKNAME_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset(
            "request",
            "Theo yêu cầu của thành viên",
            "Đổi biệt danh theo yêu cầu của thành viên",
            "Thành viên yêu cầu cập nhật biệt danh",
        ),
        ReasonPreset(
            "inappropriate",
            "Biệt danh không phù hợp",
            "Thay biệt danh không phù hợp với nội quy server",
            "Biệt danh cũ vi phạm nội quy",
        ),
        ReasonPreset(
            "format",
            "Chuẩn hóa tên hiển thị",
            "Chuẩn hóa tên hiển thị của thành viên",
            "Cập nhật theo quy ước tên của server",
        ),
        ReasonPreset(
            "moderator",
            "Quyết định của moderator",
            "Thực hiện theo quyết định của đội ngũ moderator",
            "Quyết định quản trị",
        ),
    ),
    select_placeholder="Chọn lý do đổi biệt danh",
    custom_title="Nhập lý do đổi biệt danh",
)


@dataclass(frozen=True)
class NicknameRequest:
    target_id: int
    nickname: str
    reason: str


def normalize_nickname(value: str) -> str:
    """Validate a non-empty Discord nickname without altering internal spacing."""
    nickname = value.strip()
    if not nickname:
        raise ValueError("Biệt danh không được để trống.")
    if len(nickname) > MAX_NICKNAME_LENGTH:
        raise ValueError("Biệt danh không được dài quá 32 ký tự.")
    return nickname


def nickname_change_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    """Return why a nickname change is unsafe under the current live state."""
    target_guild = getattr(target, "guild", None)
    if target_guild is not None and target_guild.id != guild.id:
        return "Thành viên cần đổi biệt danh không thuộc server này."
    if target_guild is None and guild.get_member(target.id) is not target:
        return "Thành viên cần đổi biệt danh không thuộc server này."

    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_nicknames", False):
        return "Bạn không còn quyền Manage Nicknames."
    if target.id == moderator.id:
        return "Không thể dùng lệnh moderator này để đổi biệt danh của chính mình."
    if target.id == guild.owner_id:
        return "Không thể đổi biệt danh của server owner."
    if moderator.id != guild.owner_id and target.top_role >= moderator.top_role:
        return "Bạn không thể đổi biệt danh của member có role ngang hoặc cao hơn."

    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_nicknames", False):
        return "Bot không có quyền Manage Nicknames."
    if target.id == bot_member.id:
        return "Bot không thể tự đổi biệt danh bằng lệnh này."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của member cần đổi biệt danh."
    return None


def nickname_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    """Check live actor and bot permissions without requiring a live target."""
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_nicknames", False):
        return "Bạn không còn quyền Manage Nicknames."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_nicknames", False):
        return "Bot không có quyền Manage Nicknames."
    return None


def _nickname_answer(value: str) -> FormAnswer:
    nickname = normalize_nickname(value)
    return FormAnswer(nickname, nickname)


NICKNAME_WORKFLOW_SPEC = WorkflowSpec(
    namespace="nickchange",
    title="Đổi biệt danh",
    action_text="đổi biệt danh của thành viên",
    confirm_label="Có, đổi biệt danh",
    fields=(
        ModalField(
            "nickname",
            "Biệt danh mới",
            ModalInput(
                title="Nhập biệt danh mới",
                label="Biệt danh",
                placeholder="Từ 1 đến 32 ký tự",
                min_length=1,
                max_length=MAX_NICKNAME_LENGTH,
                parser=_nickname_answer,
                button_label="Nhập biệt danh",
                button_emoji="✏️",
            ),
        ),
    ),
    reason=NICKNAME_REASON_CONFIG,
    icon="✏️",
)


class NicknameCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _submit_nickname(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: NicknameRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh đổi biệt danh chỉ dùng được trong server.")

        moderator = interaction.user
        target = guild.get_member(request.target_id)
        if target is None:
            return ActionResult(
                True,
                "Thành viên cần đổi biệt danh không còn ở trong server.",
            )

        denial = nickname_change_denial(guild, moderator, target)
        if denial is not None:
            return ActionResult(False, denial)

        nickname = normalize_nickname(request.nickname)
        reason = clean_case_reason(request.reason)
        if getattr(target, "nick", None) == nickname:
            return ActionResult(
                True,
                (
                    f"{safe_ui_text(str(target), max_length=100)} "
                    f"đã có biệt danh `{safe_ui_text(nickname, max_length=32)}`."
                ),
            )

        try:
            await target.edit(
                nick=nickname,
                reason=format_audit_reason(reason, moderator),
            )
        except discord.NotFound:
            return ActionResult(
                True,
                "Thành viên cần đổi biệt danh không còn ở trong server.",
            )
        except discord.Forbidden:
            return ActionResult(
                False,
                "Bot không thể đổi biệt danh này. Hãy kiểm tra quyền và thứ bậc role.",
            )
        except discord.HTTPException:
            logger.exception(
                "Discord rejected nickname change target=%s moderator=%s",
                target.id,
                moderator.id,
            )
            return ActionResult(
                False,
                "Discord từ chối thao tác đổi biệt danh. Vui lòng thử lại.",
            )

        return ActionResult(
            True,
            (
                f"Đã đổi biệt danh của "
                f"{safe_ui_text(str(target), max_length=100)} (`{target.id}`) "
                f"thành `{safe_ui_text(nickname, max_length=32)}`.\n"
                f"Lý do: {safe_ui_text(reason)}"
            ),
        )

    @commands.command(
        name="nickchange",
        help="Đổi biệt danh khi nhập đủ member và tên; thiếu tên để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_nicknames=True)
    @commands.cooldown(
        1,
        NICKNAME_COMMAND_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def change_nickname(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        new_nickname: str | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if member is not None or new_nickname is not None:
                await ctx.reply(
                    (
                        "Khi đổi biệt danh bằng reply, chỉ dùng lệnh "
                        f"`{ctx.clean_prefix}nickchange` không kèm tham số; "
                        "hãy nhập biệt danh trong bảng."
                    ),
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
                (
                    f"Hãy mention member bằng `{ctx.clean_prefix}nickchange @user` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}nickchange`."
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        denial = nickname_change_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        initial_answers: dict[str, FormAnswer] = {}
        if new_nickname is not None:
            try:
                initial_answers["nickname"] = _nickname_answer(new_nickname)
            except ValueError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            await run_prefix_action(
                ctx,
                self._submit_nickname,
                NicknameRequest(
                    member.id,
                    str(initial_answers["nickname"].value),
                    clean_case_reason(None),
                ),
            )
            return

        def request_builder(values, reason: str | None) -> NicknameRequest:
            answer = values["nickname"]
            return NicknameRequest(
                target_id=member.id,
                nickname=normalize_nickname(str(answer.value)),
                reason=clean_case_reason(reason),
            )

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            live_target = guild.get_member(member.id)
            if live_target is None:
                return "Thành viên cần đổi biệt danh không còn ở trong server."
            return nickname_change_denial(guild, moderator, live_target)

        view = ConfigurableModerationView(
            spec=NICKNAME_WORKFLOW_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=self._submit_nickname,
            request_builder=request_builder,
            live_permission_check=live_permission_check,
            initial_answers=initial_answers,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @change_nickname.error
    async def change_nickname_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Manage Nicknames.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy thành viên cần đổi biệt danh.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Mục tiêu đổi biệt danh không hợp lệ.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh nickchange chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử đổi biệt danh lại sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NicknameCog(bot))
