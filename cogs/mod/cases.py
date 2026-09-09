import logging
from dataclasses import dataclass

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from cogs.mod._case_helpers import clean_case_reason, normalize_case_status
from cogs.mod._interaction_ui import (
    ActionResult,
    ChannelField,
    ChoiceField,
    ChoiceOption,
    ConfigurableModerationView,
    FormAnswer,
    ModalField,
    ModalInput,
    PrefixModerationContext,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
)


logger = logging.getLogger(__name__)

CASES_COLLECTION = "moderation_cases"
CONFIG_COLLECTION = "moderation_config"
COUNTERS_COLLECTION = "feature_counters"
MAX_HISTORY_RESULTS = 10
CASE_ACTION_COOLDOWN_SECONDS = 5

ACTION_LABELS = {
    "ban": "Ban",
    "unban": "Unban",
    "kick": "Kick",
    "mute": "Mute",
    "softban": "Softban",
    "timeout": "Timeout",
    "unmute": "Unmute",
    "unsoftban": "Unsoftban",
    "untimeout": "Untimeout",
    "warn": "Warn",
}


@dataclass(frozen=True)
class CaseEditRequest:
    case_number: int
    expected_reason: str
    expected_updated_at: object
    new_reason: str


@dataclass(frozen=True)
class CaseStatusRequest:
    case_number: int
    expected_status: str
    expected_updated_at: object
    new_status: str


@dataclass(frozen=True)
class CaseLogChannelRequest:
    channel_id: int


def _parse_case_reason(value: str) -> FormAnswer:
    reason = clean_case_reason(value)
    return FormAnswer(reason, reason)


CASE_EDIT_SPEC = WorkflowSpec(
    namespace="case-edit",
    title="Sửa lý do moderation case",
    action_text="cập nhật lý do của case",
    confirm_label="Có, cập nhật case",
    fields=(
        ModalField(
            "new_reason",
            "Lý do mới",
            ModalInput(
                title="Lý do mới của case",
                label="Lý do mới",
                placeholder="Nhập lý do thay thế",
                max_length=1000,
                style=discord.TextStyle.paragraph,
                parser=_parse_case_reason,
                button_label="Nhập lý do mới",
                button_emoji="✏️",
            ),
        ),
    ),
    icon="🗂️",
)

CASE_STATUS_SPEC = WorkflowSpec(
    namespace="case-status",
    title="Đổi trạng thái moderation case",
    action_text="thay đổi trạng thái của case",
    confirm_label="Có, đổi trạng thái",
    fields=(
        ChoiceField(
            "status",
            "Trạng thái mới",
            (
                ChoiceOption("open", "Open", "open"),
                ChoiceOption("resolved", "Resolved", "resolved"),
                ChoiceOption("appealed", "Appealed", "appealed"),
                ChoiceOption("void", "Void", "void"),
            ),
            placeholder="Chọn trạng thái mới",
        ),
    ),
    icon="🗂️",
)

CASE_LOG_CHANNEL_SPEC = WorkflowSpec(
    namespace="case-log-channel",
    title="Đổi kênh moderation log",
    action_text="đổi kênh ghi moderation case",
    confirm_label="Có, đổi kênh log",
    fields=(ChannelField("channel_id", "Kênh log mới"),),
    icon="🗂️",
)


def _case_permission_denial(
    moderator: discord.Member,
    *,
    manage_guild: bool = False,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    permission_name = "manage_guild" if manage_guild else "manage_messages"
    if permissions is None or not getattr(permissions, permission_name, False):
        return (
            "Bạn không còn quyền Manage Server."
            if manage_guild
            else "Bạn không còn quyền Manage Messages."
        )
    return None


class ModerationCasesCog(commands.Cog):
    """Numbered moderation audit trail shared by moderation cogs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.cases = self.db[CASES_COLLECTION]
        self.config = self.db[CONFIG_COLLECTION]
        self.counters = self.db[COUNTERS_COLLECTION]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        try:
            self.cases.create_index(
                [("guild_id", ASCENDING), ("case_number", ASCENDING)],
                unique=True,
                name="guild_case_unique",
            )
            self.cases.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("target_id", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="guild_target_history",
            )
            self.config.create_index(
                [("guild_id", ASCENDING)], unique=True, name="guild_config_unique"
            )
        except PyMongoError:
            logger.exception("Failed to create moderation case indexes")

    def _next_case_number(self, guild_id: int) -> int:
        counter = self.counters.find_one_and_update(
            {"_id": f"moderation_case:{guild_id}"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(counter["value"])

    async def create_case(
        self,
        *,
        guild: discord.Guild,
        target: discord.abc.User,
        moderator: discord.abc.User,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
    ) -> int:
        case_number = self._next_case_number(guild.id)
        now = discord.utils.utcnow()
        document = {
            "guild_id": guild.id,
            "case_number": case_number,
            "action": action.lower(),
            "target_id": target.id,
            "target_name": str(target),
            "moderator_id": moderator.id,
            "moderator_name": str(moderator),
            "reason": clean_case_reason(reason),
            "duration_seconds": duration_seconds,
            "status": "open",
            "edit_history": [],
            "created_at": now,
            "updated_at": now,
        }
        self.cases.insert_one(document)
        await self._send_case_log(guild, document)
        return case_number

    async def _send_case_log(self, guild: discord.Guild, case: dict) -> None:
        config = self.config.find_one({"guild_id": guild.id}) or {}
        channel_id = config.get("log_channel_id")
        if not channel_id:
            return

        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(
                embed=self._case_embed(case),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Failed to send moderation log guild=%s case=%s",
                guild.id,
                case["case_number"],
            )

    @staticmethod
    def _case_embed(case: dict) -> discord.Embed:
        action = ACTION_LABELS.get(case["action"], case["action"].title())
        status = case.get("status", "open")
        embed = discord.Embed(
            title=f"Case #{case['case_number']} · {action}",
            color=discord.Color.orange(),
            timestamp=case.get("created_at"),
        )
        embed.add_field(
            name="Thành viên",
            value=f"<@{case['target_id']}> ({case['target_id']})",
            inline=False,
        )
        embed.add_field(
            name="Moderator",
            value=f"<@{case['moderator_id']}> ({case['moderator_id']})",
            inline=False,
        )
        embed.add_field(name="Lý do", value=case["reason"][:1024], inline=False)
        if case.get("duration_seconds"):
            embed.add_field(
                name="Thời hạn",
                value=f"{case['duration_seconds'] // 60:,} phút",
                inline=True,
            )
        embed.add_field(name="Trạng thái", value=status, inline=True)
        return embed

    @commands.group(
        name="case",
        invoke_without_command=True,
        help="Xem và quản lý moderation cases.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case(self, ctx: commands.Context) -> None:
        await ctx.send(
            "Dùng: case view <số>, case history <member>, case edit <số> <lý do>, "
            "case status <số> <open|resolved|appealed|void>."
        )

    @case.command(name="view")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_view(self, ctx: commands.Context, case_number: int) -> None:
        case = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if case is None:
            await ctx.send("Không tìm thấy case đó.")
            return
        await ctx.send(
            embed=self._case_embed(case),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @case.command(name="history")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    async def case_history(
        self,
        ctx: commands.Context,
        member: discord.Member,
        limit: int = MAX_HISTORY_RESULTS,
    ) -> None:
        limit = max(1, min(limit, MAX_HISTORY_RESULTS))
        cases = list(
            self.cases.find(
                {"guild_id": ctx.guild.id, "target_id": member.id}
            )
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        if not cases:
            await ctx.send(f"{member.mention} chưa có moderation case nào.")
            return

        lines = []
        for case in cases:
            action = ACTION_LABELS.get(case["action"], case["action"].title())
            reason = case["reason"]
            if len(reason) > 80:
                reason = reason[:77] + "..."
            lines.append(
                f"#{case['case_number']} · {action} · {case.get('status', 'open')} — {reason}"
            )
        embed = discord.Embed(
            title=f"Lịch sử moderation · {member}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @case.command(name="edit")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    @commands.cooldown(
        1,
        CASE_ACTION_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def case_edit(
        self,
        ctx: commands.Context,
        case_number: int,
        *,
        reason: str | None = None,
    ) -> None:
        if case_number < 1:
            await ctx.send("Số case phải lớn hơn 0.")
            return
        existing = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if existing is None:
            await ctx.send("Không tìm thấy case đó.")
            return

        expected_reason = existing.get("reason", "Không có lý do cụ thể")
        expected_updated_at = existing.get("updated_at")

        def build_request(answers, _reason) -> CaseEditRequest:
            return CaseEditRequest(
                case_number=case_number,
                expected_reason=expected_reason,
                expected_updated_at=expected_updated_at,
                new_reason=clean_case_reason(answers["new_reason"].value),
            )

        async def submit_edit(
            interaction: discord.Interaction | PrefixModerationContext,
            request: CaseEditRequest,
        ) -> ActionResult:
            denial = _case_permission_denial(interaction.user)
            if denial is not None:
                return ActionResult(False, denial)
            if request.new_reason == request.expected_reason:
                current = self.cases.find_one(
                    {
                        "guild_id": interaction.guild.id,
                        "case_number": request.case_number,
                        "reason": request.expected_reason,
                        "updated_at": request.expected_updated_at,
                    },
                    {"_id": 1},
                )
                if current is None:
                    return ActionResult(
                        True,
                        (
                            f"Case #{request.case_number} đã thay đổi sau khi bảng "
                            "được mở; không xác nhận dữ liệu cũ."
                        ),
                    )
                return ActionResult(
                    True,
                    f"Case #{request.case_number} đã có lý do này; không thay đổi dữ liệu.",
                )

            now = discord.utils.utcnow()
            case = self.cases.find_one_and_update(
                {
                    "guild_id": interaction.guild.id,
                    "case_number": request.case_number,
                    "reason": request.expected_reason,
                    "updated_at": request.expected_updated_at,
                },
                {
                    "$set": {
                        "reason": request.new_reason,
                        "updated_at": now,
                        "updated_by": interaction.user.id,
                    },
                    "$push": {
                        "edit_history": {
                            "field": "reason",
                            "old_value": request.expected_reason,
                            "new_value": request.new_reason,
                            "editor_id": interaction.user.id,
                            "edited_at": now,
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if case is None:
                return ActionResult(
                    True,
                    (
                        f"Case #{request.case_number} đã thay đổi sau khi bảng được mở; "
                        "không ghi đè dữ liệu mới hơn."
                    ),
                )
            await self._send_case_log(interaction.guild, case)
            return ActionResult(
                True,
                f"Đã cập nhật lý do cho case #{request.case_number}.",
            )

        if reason is not None:
            answer = _parse_case_reason(reason)
            await run_prefix_action(
                ctx,
                submit_edit,
                build_request({"new_reason": answer}, None),
            )
            return

        view = ConfigurableModerationView(
            spec=CASE_EDIT_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(case_number, f"Case #{case_number}"),
            submitter=submit_edit,
            request_builder=build_request,
            live_permission_check=lambda _guild, moderator: _case_permission_denial(
                moderator
            ),
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @case.command(name="status")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    @commands.cooldown(
        1,
        CASE_ACTION_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def case_status(
        self,
        ctx: commands.Context,
        case_number: int,
        status: str | None = None,
    ) -> None:
        if case_number < 1:
            await ctx.send("Số case phải lớn hơn 0.")
            return
        existing = self.cases.find_one(
            {"guild_id": ctx.guild.id, "case_number": case_number}
        )
        if existing is None:
            await ctx.send("Không tìm thấy case đó.")
            return

        initial_status = None
        if status is not None:
            try:
                initial_status = normalize_case_status(status)
            except ValueError:
                await ctx.send("Trạng thái phải là open, resolved, appealed hoặc void.")
                return
        expected_status = existing.get("status", "open")
        expected_updated_at = existing.get("updated_at")

        def build_request(answers, _reason) -> CaseStatusRequest:
            return CaseStatusRequest(
                case_number=case_number,
                expected_status=expected_status,
                expected_updated_at=expected_updated_at,
                new_status=normalize_case_status(str(answers["status"].value)),
            )

        async def submit_status(
            interaction: discord.Interaction | PrefixModerationContext,
            request: CaseStatusRequest,
        ) -> ActionResult:
            denial = _case_permission_denial(interaction.user)
            if denial is not None:
                return ActionResult(False, denial)
            if request.new_status == request.expected_status:
                current = self.cases.find_one(
                    {
                        "guild_id": interaction.guild.id,
                        "case_number": request.case_number,
                        "status": request.expected_status,
                        "updated_at": request.expected_updated_at,
                    },
                    {"_id": 1},
                )
                if current is None:
                    return ActionResult(
                        True,
                        (
                            f"Case #{request.case_number} đã thay đổi sau khi bảng "
                            "được mở; không xác nhận trạng thái cũ."
                        ),
                    )
                return ActionResult(
                    True,
                    f"Case #{request.case_number} đã ở trạng thái {request.new_status}.",
                )

            now = discord.utils.utcnow()
            case = self.cases.find_one_and_update(
                {
                    "guild_id": interaction.guild.id,
                    "case_number": request.case_number,
                    "status": request.expected_status,
                    "updated_at": request.expected_updated_at,
                },
                {
                    "$set": {
                        "status": request.new_status,
                        "updated_at": now,
                        "updated_by": interaction.user.id,
                    },
                    "$push": {
                        "edit_history": {
                            "field": "status",
                            "old_value": request.expected_status,
                            "new_value": request.new_status,
                            "editor_id": interaction.user.id,
                            "edited_at": now,
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if case is None:
                return ActionResult(
                    True,
                    (
                        f"Case #{request.case_number} đã thay đổi sau khi bảng được mở; "
                        "không ghi đè trạng thái mới hơn."
                    ),
                )
            await self._send_case_log(interaction.guild, case)
            return ActionResult(
                True,
                (
                    f"Đã chuyển case #{request.case_number} "
                    f"sang trạng thái {request.new_status}."
                ),
            )

        if initial_status is not None:
            await run_prefix_action(
                ctx,
                submit_status,
                build_request(
                    {"status": FormAnswer(initial_status, initial_status.title())},
                    None,
                ),
            )
            return

        view = ConfigurableModerationView(
            spec=CASE_STATUS_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=WorkflowTarget(case_number, f"Case #{case_number}"),
            submitter=submit_status,
            request_builder=build_request,
            live_permission_check=lambda _guild, moderator: _case_permission_denial(
                moderator
            ),
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @case.command(name="log_channel")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(
        1,
        CASE_ACTION_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def case_log_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None = None,
    ) -> None:
        initial_channel = channel or (
            ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None
        )

        def build_request(answers, _reason) -> CaseLogChannelRequest:
            return CaseLogChannelRequest(channel_id=int(answers["channel_id"].value))

        async def submit_log_channel(
            interaction: discord.Interaction | PrefixModerationContext,
            request: CaseLogChannelRequest,
        ) -> ActionResult:
            denial = _case_permission_denial(interaction.user, manage_guild=True)
            if denial is not None:
                return ActionResult(False, denial)
            target = interaction.guild.get_channel(request.channel_id)
            if not isinstance(target, discord.TextChannel):
                return ActionResult(
                    False,
                    "Hãy chọn một text channel đang tồn tại trong server.",
                )
            bot_member = interaction.guild.me
            if bot_member is None:
                return ActionResult(False, "Không thể xác định member của bot.")
            permissions = target.permissions_for(bot_member)
            if not (
                permissions.view_channel
                and permissions.send_messages
                and permissions.embed_links
            ):
                return ActionResult(
                    False,
                    "Bot cần View Channel, Send Messages và Embed Links trong kênh log.",
                )
            self.config.update_one(
                {"guild_id": interaction.guild.id},
                {
                    "$set": {
                        "log_channel_id": target.id,
                        "updated_at": discord.utils.utcnow(),
                        "updated_by": interaction.user.id,
                    }
                },
                upsert=True,
            )
            return ActionResult(
                True,
                f"Moderation cases sẽ được ghi vào #{target.name} (`{target.id}`).",
            )

        if channel is not None:
            await run_prefix_action(
                ctx,
                submit_log_channel,
                CaseLogChannelRequest(channel_id=channel.id),
            )
            return

        view = ConfigurableModerationView(
            spec=CASE_LOG_CHANNEL_SPEC,
            author_id=ctx.author.id,
            guild_id=ctx.guild.id,
            target=None,
            submitter=submit_log_channel,
            request_builder=build_request,
            live_permission_check=lambda _guild, moderator: _case_permission_denial(
                moderator,
                manage_guild=True,
            ),
            initial_answers=(
                {
                    "channel_id": FormAnswer(
                        initial_channel.id,
                        f"#{initial_channel.name} (`{initial_channel.id}`)",
                    )
                }
                if initial_channel is not None
                else None
            ),
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @case.error
    async def case_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Messages hoặc Manage Server.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Tham số case không hợp lệ.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"Hãy thử mở bảng case lại sau {error.retry_after:.1f} giây."
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModerationCasesCog(bot))
