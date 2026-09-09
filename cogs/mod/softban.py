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
SOFTBAN_COMMAND_COOLDOWN_SECONDS = 5
SOFTBAN_ROLE_NAME = "Tù ngay"

UNSOFTBAN_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset("expired", "Đã hết thời hạn", "Đã hoàn thành thời hạn xử lý"),
        ReasonPreset("appeal", "Chấp nhận kháng nghị", "Kháng nghị được chấp nhận"),
        ReasonPreset("mistake", "Xử lý nhầm", "Gỡ softban do xử lý nhầm"),
        ReasonPreset("moderator", "Quyết định của moderator", "Moderator gỡ softban"),
    ),
    select_placeholder="Chọn lý do gỡ softban",
    custom_title="Nhập lý do gỡ softban",
)

SOFTBAN_WORKFLOW_SPEC = WorkflowSpec(
    namespace="softban",
    title="Nhốt thành viên",
    action_text="thay role hiện tại bằng Tù ngay",
    confirm_label="Có, nhốt thành viên",
    reason=COMMON_REASON_CONFIG,
    icon="⛓️",
    confirm_color=0xED4245,
)
UNSOFTBAN_WORKFLOW_SPEC = WorkflowSpec(
    namespace="unsoftban",
    title="Khôi phục thành viên",
    action_text="gỡ Tù ngay và khôi phục role cũ",
    confirm_label="Có, khôi phục role",
    reason=UNSOFTBAN_REASON_CONFIG,
    icon="🔓",
    confirm_color=0x57F287,
    confirm_style=discord.ButtonStyle.success,
)


@dataclass(frozen=True)
class SoftbanRequest:
    target_id: int
    remove: bool
    reason: str


def softban_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "ban_members", False):
        return "Bạn không còn quyền Ban Members."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_roles", False):
        return "Bot không có quyền Manage Roles để thay đổi role thành viên."
    return None


def softban_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    denial = softban_permission_denial(guild, moderator)
    if denial is not None:
        return denial
    if target.guild.id != guild.id:
        return "Thành viên cần xử lý không thuộc server này."
    if not can_moderate(moderator, target):
        return "Bạn không thể xử lý chính mình, server owner, hoặc role ngang/cao hơn."
    bot_member = guild.me
    if target.id == bot_member.id:
        return "Bot không thể tự thay đổi role của chính mình."
    if bot_member.id != guild.owner_id and target.top_role >= bot_member.top_role:
        return "Role cao nhất của bot phải cao hơn role của thành viên cần xử lý."
    return None


class SoftbanCog(commands.Cog):
    """Temporarily jail a member by replacing their roles with Tù ngay."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.old_roles = self.db["old_roles"]

    def save_old_roles(
        self,
        guild_id: int,
        member_id: int,
        role_ids: list[int],
    ) -> bool:
        query = {"guild_id": guild_id, "member_id": member_id}
        existing = self.old_roles.find_one(query)
        if existing is None:
            legacy = self.old_roles.find_one(
                {"member_id": member_id, "guild_id": {"$exists": False}}
            )
            if legacy is not None:
                self.old_roles.update_one(
                    {"_id": legacy["_id"]},
                    {
                        "$set": {
                            "guild_id": guild_id,
                            "updated_at": discord.utils.utcnow(),
                        }
                    },
                )
                return False
        else:
            return False
        self.old_roles.update_one(
            query,
            {
                "$setOnInsert": {
                    "guild_id": guild_id,
                    "member_id": member_id,
                    "old_roles": role_ids,
                    "updated_at": discord.utils.utcnow(),
                }
            },
            upsert=True,
        )
        return True

    def get_old_roles(self, guild_id: int, member_id: int) -> list[int] | None:
        document = self.old_roles.find_one(
            {"guild_id": guild_id, "member_id": member_id}
        )
        if document is None:
            document = self.old_roles.find_one(
                {"member_id": member_id, "guild_id": {"$exists": False}}
            )
            if document is not None:
                self.old_roles.update_one(
                    {"_id": document["_id"]},
                    {
                        "$set": {
                            "guild_id": guild_id,
                            "updated_at": discord.utils.utcnow(),
                        }
                    },
                )
        return document.get("old_roles", []) if document else None

    @staticmethod
    async def _restore_roles_after_failure(
        member: discord.Member,
        roles: list[discord.Role],
        moderator: discord.Member,
    ) -> bool:
        if not roles:
            return True
        try:
            await member.add_roles(
                *roles,
                reason=f"Rollback failed softban requested by {moderator}",
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed to roll back softban target=%s", member.id)
            return False
        return True

    async def _apply_softban(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        member: discord.Member,
        reason: str,
    ) -> ActionResult:
        handcuffed_role = discord.utils.get(guild.roles, name=SOFTBAN_ROLE_NAME)
        if handcuffed_role is None:
            return ActionResult(False, f"Role {SOFTBAN_ROLE_NAME} không tồn tại.")
        if handcuffed_role >= guild.me.top_role:
            return ActionResult(
                False,
                f"Role {SOFTBAN_ROLE_NAME} phải thấp hơn role cao nhất của bot.",
            )
        if handcuffed_role in member.roles:
            return ActionResult(True, f"{member} (`{member.id}`) đã bị nhốt.")

        original_roles = [
            role for role in member.roles if not role.is_default() and not role.managed
        ]
        created_snapshot = self.save_old_roles(
            guild.id,
            member.id,
            [role.id for role in original_roles],
        )
        try:
            await member.edit(roles=[], reason=format_audit_reason(reason, moderator))
        except discord.Forbidden:
            if created_snapshot:
                self.old_roles.delete_one(
                    {"guild_id": guild.id, "member_id": member.id}
                )
            return ActionResult(False, "Bot không thể thay đổi role của thành viên này.")
        except discord.HTTPException:
            if created_snapshot:
                self.old_roles.delete_one(
                    {"guild_id": guild.id, "member_id": member.id}
                )
            logger.exception("Discord rejected softban target=%s", member.id)
            return ActionResult(False, "Discord từ chối thao tác softban.")

        try:
            await member.add_roles(
                handcuffed_role,
                reason=format_audit_reason(reason, moderator),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed to add %s role target=%s",
                SOFTBAN_ROLE_NAME,
                member.id,
            )
            restorable = [role for role in original_roles if role < guild.me.top_role]
            restored = await self._restore_roles_after_failure(
                member,
                restorable,
                moderator,
            )
            if restored:
                if created_snapshot:
                    self.old_roles.delete_one(
                        {"guild_id": guild.id, "member_id": member.id}
                    )
                return ActionResult(
                    False,
                    f"Không thể gán role {SOFTBAN_ROLE_NAME}; các role cũ đã được khôi phục.",
                )
            return ActionResult(
                False,
                (
                    f"Không thể gán {SOFTBAN_ROLE_NAME} hoặc tự khôi phục role; "
                    "dữ liệu role cũ vẫn được giữ."
                ),
            )

        case_number = await record_case(
            self.bot,
            guild=guild,
            target=member,
            moderator=moderator,
            action="softban",
            reason=reason,
        )
        return ActionResult(
            True,
            f"Đã nhốt {member} (`{member.id}`). Lý do: {reason}{case_suffix(case_number)}",
        )

    async def _apply_unsoftban(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        member: discord.Member,
        reason: str,
    ) -> ActionResult:
        old_role_ids = self.get_old_roles(guild.id, member.id)
        if old_role_ids is None:
            return ActionResult(True, f"Không tìm thấy role cũ cho {member} (`{member.id}`).")
        handcuffed_role = discord.utils.get(guild.roles, name=SOFTBAN_ROLE_NAME)
        restorable = []
        for role_id in old_role_ids:
            role = guild.get_role(role_id)
            if role and not role.managed and role < guild.me.top_role:
                restorable.append(role)
        try:
            if handcuffed_role and handcuffed_role in member.roles:
                await member.remove_roles(
                    handcuffed_role,
                    reason=format_audit_reason(reason, moderator),
                )
            if restorable:
                await member.add_roles(
                    *restorable,
                    reason=format_audit_reason(reason, moderator),
                )
        except discord.Forbidden:
            return ActionResult(False, "Bot không thể khôi phục role của thành viên này.")
        except discord.HTTPException:
            logger.exception("Discord rejected unsoftban target=%s", member.id)
            return ActionResult(False, "Discord từ chối thao tác unsoftban.")

        self.old_roles.delete_one({"guild_id": guild.id, "member_id": member.id})
        case_number = await record_case(
            self.bot,
            guild=guild,
            target=member,
            moderator=moderator,
            action="unsoftban",
            reason=reason,
        )
        return ActionResult(
            True,
            f"Đã thả {member} (`{member.id}`) và khôi phục role{case_suffix(case_number)}.",
        )

    async def _submit_softban(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: SoftbanRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh softban chỉ dùng được trong server.")
        key = (guild.id, request.target_id)
        if key in ACTIVE_ROLE_MUTATION_TARGETS:
            return ActionResult(False, "Một thao tác role khác cho thành viên này đang chạy.")
        ACTIVE_ROLE_MUTATION_TARGETS.add(key)
        try:
            try:
                member = await guild.fetch_member(request.target_id)
            except discord.NotFound:
                return ActionResult(True, "Thành viên cần xử lý không còn ở trong server.")
            except discord.Forbidden:
                return ActionResult(False, "Bot không thể tải lại thông tin thành viên.")
            except discord.HTTPException:
                logger.exception("Could not refresh softban target=%s", request.target_id)
                return ActionResult(False, "Không thể kiểm tra thành viên lúc này.")
            moderator = interaction.user
            denial = softban_target_denial(guild, moderator, member)
            if denial is not None:
                return ActionResult(False, denial)
            reason = clean_case_reason(request.reason)
            if request.remove:
                return await self._apply_unsoftban(guild, moderator, member, reason)
            return await self._apply_softban(guild, moderator, member, reason)
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(key)

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

    async def _open_workflow(
        self,
        ctx: commands.Context,
        member: discord.Member,
        reason: str | None,
        *,
        remove: bool,
    ) -> None:
        denial = softban_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(denial, mention_author=False)
            return
        view = ConfigurableModerationView(
            spec=UNSOFTBAN_WORKFLOW_SPEC if remove else SOFTBAN_WORKFLOW_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(member.id, str(member)),
            submitter=self._submit_softban,
            request_builder=lambda _values, selected_reason: SoftbanRequest(
                member.id,
                remove,
                clean_case_reason(selected_reason),
            ),
            live_permission_check=softban_permission_denial,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(
        name="softban",
        help="Nhốt member trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.cooldown(1, SOFTBAN_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def softban_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(ctx, member, reason, command_name="softban")
        if member is not None:
            if ctx.message.reference is None:
                await run_prefix_action(
                    ctx,
                    self._submit_softban,
                    SoftbanRequest(member.id, False, clean_case_reason(reason)),
                )
                return
            await self._open_workflow(ctx, member, reason, remove=False)

    @commands.command(
        name="unsoftban",
        help="Khôi phục role trực tiếp; reply không kèm đối số để mở bảng.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(ban_members=True)
    @commands.cooldown(1, SOFTBAN_COMMAND_COOLDOWN_SECONDS, commands.BucketType.member)
    async def unsoftban_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        member = await self._resolve_target(ctx, member, reason, command_name="unsoftban")
        if member is not None:
            if ctx.message.reference is None:
                await run_prefix_action(
                    ctx,
                    self._submit_softban,
                    SoftbanRequest(
                        member.id,
                        True,
                        clean_case_reason(
                            "Moderator removed softban" if reason is None else reason
                        ),
                    ),
                )
                return
            await self._open_workflow(ctx, member, reason, remove=True)

    @softban_member.error
    @unsoftban_member.error
    async def softban_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
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
    await bot.add_cog(SoftbanCog(bot))
