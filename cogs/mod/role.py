import asyncio
import logging
from dataclasses import dataclass

import discord
from discord.ext import commands

from cogs.mod._case_helpers import clean_case_reason, format_audit_reason
from cogs.mod._interaction_ui import (
    ActionResult,
    ConfigurableModerationView,
    FormAnswer,
    PrefixModerationContext,
    ReasonConfig,
    ReasonPreset,
    RoleField,
    UserField,
    WorkflowSpec,
    WorkflowTarget,
    run_prefix_action,
    safe_ui_text,
)
from cogs.mod._member_state import ACTIVE_ROLE_MUTATION_TARGETS
from cogs.mod._reply_target import (
    ReplyTargetError,
    resolve_same_channel_reply_member,
)


logger = logging.getLogger(__name__)

ROLE_ROLL_SELECT_CUSTOM_ID = "roleroll:field:role_id"
ROLE_UNROLL_SELECT_CUSTOM_ID = "roleunroll:field:role_id"
ROLE_CHANGE_COOLDOWN_SECONDS = 5
ROLE_COPY_COOLDOWN_SECONDS = 15
ROLE_COPY_MESSAGE_MAX_LENGTH = 2000
ROLE_TABLE_NAME_MAX_LENGTH = 48

ROLE_ASSIGN_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset(
            "request",
            "Theo yêu cầu của thành viên",
            "Gán role theo yêu cầu của thành viên",
            "Thành viên được duyệt nhận role này",
        ),
        ReasonPreset(
            "access",
            "Cấp quyền truy cập",
            "Cấp quyền kênh hoặc tính năng bằng role",
            "Mở quyền truy cập cần thiết",
        ),
        ReasonPreset(
            "event",
            "Sự kiện hoặc hoạt động",
            "Gán role cho sự kiện hoặc hoạt động",
            "Role tạm thời cho event",
        ),
        ReasonPreset(
            "restore",
            "Khôi phục role",
            "Khôi phục role thành viên đã có trước đó",
            "Trả lại role bị mất hoặc gỡ nhầm",
        ),
        ReasonPreset(
            "moderator",
            "Quyết định của moderator",
            "Gán role theo quyết định của đội ngũ moderator",
            "Quyết định quản trị",
        ),
    ),
    select_placeholder="Chọn lý do gán role",
    custom_title="Nhập lý do gán role",
    custom_placeholder="Ví dụ: duyệt đơn xin role Helper",
)

ROLE_REMOVE_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset(
            "request",
            "Theo yêu cầu của thành viên",
            "Gỡ role theo yêu cầu của thành viên",
            "Thành viên không còn muốn giữ role",
        ),
        ReasonPreset(
            "expired",
            "Hết hạn hoặc kết thúc sự kiện",
            "Gỡ role vì hết hạn hoặc sự kiện đã kết thúc",
            "Role tạm thời không còn hiệu lực",
        ),
        ReasonPreset(
            "incorrect",
            "Gán nhầm",
            "Gỡ role được gán nhầm",
            "Sửa một lần gán role không đúng",
        ),
        ReasonPreset(
            "no-longer",
            "Không còn phù hợp",
            "Gỡ role vì thành viên không còn đủ điều kiện",
            "Role không còn đúng với thành viên",
        ),
        ReasonPreset(
            "moderator",
            "Quyết định của moderator",
            "Gỡ role theo quyết định của đội ngũ moderator",
            "Quyết định quản trị",
        ),
    ),
    select_placeholder="Chọn lý do gỡ role",
    custom_title="Nhập lý do gỡ role",
    custom_placeholder="Ví dụ: hết hạn role sự kiện Tết",
)

ROLE_COPY_REASON_CONFIG = ReasonConfig(
    presets=(
        ReasonPreset(
            "sync",
            "Đồng bộ quyền",
            "Sao chép role để đồng bộ quyền giữa hai thành viên",
            "Làm bộ role của đích giống nguồn",
        ),
        ReasonPreset(
            "account",
            "Chuyển tài khoản",
            "Chuyển role sang tài khoản khác của cùng thành viên",
            "Giữ quyền khi đổi acc",
        ),
        ReasonPreset(
            "setup",
            "Thiết lập thành viên mới",
            "Cấp cùng bộ role cho thành viên mới",
            "Setup nhanh theo mẫu có sẵn",
        ),
        ReasonPreset(
            "restore",
            "Khôi phục sau khi mất role",
            "Khôi phục role từ thành viên mẫu",
            "Trả lại quyền bị mất",
        ),
        ReasonPreset(
            "moderator",
            "Quyết định của moderator",
            "Sao chép role theo quyết định của đội ngũ moderator",
            "Quyết định quản trị",
        ),
    ),
    select_placeholder="Chọn lý do sao chép role",
    custom_title="Nhập lý do sao chép role",
    custom_placeholder="Ví dụ: chuyển role sang acc mới",
)


@dataclass(frozen=True)
class RoleChangeRequest:
    target_id: int
    role_id: int
    remove: bool
    reason: str


@dataclass(frozen=True)
class RoleCopyRequest:
    source_id: int
    target_id: int
    role_ids: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class RoleCopyPlan:
    eligible: tuple[discord.Role, ...]
    already_present: tuple[discord.Role, ...]
    unmanageable: tuple[discord.Role, ...]


def role_permission_denial(
    guild: discord.Guild,
    moderator: discord.Member,
) -> str | None:
    permissions = getattr(moderator, "guild_permissions", None)
    if permissions is None or not getattr(permissions, "manage_roles", False):
        return "Bạn không còn quyền Manage Roles để thực hiện thao tác này."
    bot_member = guild.me
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    if bot_member is None or not getattr(bot_permissions, "manage_roles", False):
        return "Bot không có quyền Manage Roles trong server này."
    return None


def role_target_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
) -> str | None:
    """Return why this moderator or bot cannot change roles on the target."""
    denial = role_permission_denial(guild, moderator)
    if denial is not None:
        return denial

    target_guild = getattr(target, "guild", None)
    if target_guild is not None and target_guild.id != guild.id:
        return "Thành viên cần xử lý role không thuộc server này."

    if moderator.id != guild.owner_id and target.top_role >= moderator.top_role:
        return (
            "Bạn không thể thay đổi role của thành viên có role ngang hoặc cao hơn."
        )

    bot_member = guild.me
    if (
        bot_member is not None
        and bot_member.id != guild.owner_id
        and target.top_role >= bot_member.top_role
    ):
        return "Role cao nhất của bot phải cao hơn role của thành viên cần xử lý."
    return None


def _role_manageability_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    role: discord.Role,
    *,
    action: str,
) -> str | None:
    role_guild = getattr(role, "guild", None)
    if role_guild is not None and role_guild.id != guild.id:
        return "Role đã chọn không thuộc server này."
    if role.is_default():
        return f"Không thể {action} role mặc định `@everyone`."
    if role.managed:
        return (
            "Role này do Discord hoặc integration quản lý nên "
            f"không thể {action} thủ công."
        )

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        return f"Bot không có quyền Manage Roles để {action} role."
    if bot_member.id != guild.owner_id and role >= bot_member.top_role:
        return "Role đã chọn phải thấp hơn role cao nhất của bot."

    if not moderator.guild_permissions.manage_roles:
        return f"Bạn không còn quyền Manage Roles để {action} role."
    if moderator.id != guild.owner_id and role >= moderator.top_role:
        return f"Bạn chỉ có thể {action} role thấp hơn role cao nhất của mình."
    return None


def role_assignment_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
) -> str | None:
    """Return a user-facing reason when a selected role cannot be assigned."""
    denial = _role_manageability_denial(
        guild,
        moderator,
        role,
        action="gán",
    )
    if denial is not None:
        return denial
    if role in target.roles:
        return f"{target.mention} đã có role {role.mention} rồi."
    return None


def role_removal_denial(
    guild: discord.Guild,
    moderator: discord.Member,
    target: discord.Member,
    role: discord.Role,
) -> str | None:
    """Return a user-facing reason when a selected role cannot be removed."""
    denial = _role_manageability_denial(
        guild,
        moderator,
        role,
        action="gỡ",
    )
    if denial is not None:
        return denial
    if role not in target.roles:
        return f"{target.mention} không có role {role.mention}."
    return None


def plan_role_copy(
    guild: discord.Guild,
    moderator: discord.Member,
    source: discord.Member,
    target: discord.Member,
) -> RoleCopyPlan:
    """Classify source roles for an additive, hierarchy-safe copy."""
    eligible: list[discord.Role] = []
    already_present: list[discord.Role] = []
    unmanageable: list[discord.Role] = []

    for role in source.roles:
        denial = _role_manageability_denial(
            guild,
            moderator,
            role,
            action="gán",
        )
        if denial is not None:
            unmanageable.append(role)
        elif role in target.roles:
            already_present.append(role)
        else:
            eligible.append(role)

    return RoleCopyPlan(
        eligible=tuple(eligible),
        already_present=tuple(already_present),
        unmanageable=tuple(unmanageable),
    )


def format_role_copy_result(
    source: discord.Member,
    target: discord.Member,
    *,
    copied: list[discord.Role],
    failed: list[discord.Role],
    not_attempted: list[discord.Role],
    source_changed: int = 0,
    unmanageable: int = 0,
    already_present: int = 0,
    stop_reason: str | None = None,
    reason: str | None = None,
) -> str:
    if copied:
        lines = [
            f"Đã sao chép **{len(copied)}** role.",
        ]
    else:
        lines = ["Không có role mới nào được sao chép."]

    lines.extend(
        (
            f"Nguồn: {source.mention} (`{source.id}`)",
            f"Đích: {target.mention} (`{target.id}`)",
        )
    )
    if reason is not None:
        lines.append(
            f"Lý do: {safe_ui_text(clean_case_reason(reason), max_length=300)}"
        )

    detail_lines: list[str] = []
    skipped_parts: list[str] = []
    if already_present:
        skipped_parts.append(f"{already_present} role đích đã có")
    if source_changed:
        skipped_parts.append("nguồn đã thay đổi")
    if unmanageable:
        skipped_parts.append(f"{unmanageable} role không thể quản lý")
    if skipped_parts:
        detail_lines.append("Bỏ qua: " + " · ".join(skipped_parts) + ".")
    if failed or not_attempted:
        detail_lines.append(
            f"Lỗi: {len(failed)} role · Chưa thử: {len(not_attempted)} role."
        )
    if stop_reason is not None:
        detail_lines.append(stop_reason)

    if copied:
        heading = f"**Role đã sao chép ({len(copied)})**"
        fixed_text = "\n".join(lines + [heading] + detail_lines)
        table_budget = max(
            0,
            ROLE_COPY_MESSAGE_MAX_LENGTH - len(fixed_text) - 1,
        )
        role_table = format_role_table(
            tuple((role.id, role.name) for role in copied),
            max_length=table_budget,
        )
        lines.extend((heading, role_table))
    lines.extend(detail_lines)
    return "\n".join(lines)


def _safe_role_table_name(value: str) -> str:
    normalized = " ".join(value.split()).replace("`", "ˋ").replace("|", "¦")
    if not normalized:
        normalized = "Role không có tên"
    if len(normalized) <= ROLE_TABLE_NAME_MAX_LENGTH:
        return normalized
    return f"{normalized[: ROLE_TABLE_NAME_MAX_LENGTH - 1]}…"


def format_role_table(
    roles: tuple[tuple[int, str], ...],
    *,
    max_length: int,
) -> str:
    """Return a bounded plain-text table of role names and IDs."""
    if not roles or max_length <= 0:
        return ""

    prefix = ("```text", "# | Role | ID", "--|------|---")
    closing = "```"
    rendered_rows: list[str] = []
    for index, (role_id, role_name) in enumerate(roles, start=1):
        row = f"{index} | {_safe_role_table_name(role_name)} | {role_id}"
        candidate_rows = rendered_rows + [row]
        omitted = len(roles) - len(candidate_rows)
        suffix = [f"… | +{omitted} role khác |"] if omitted else []
        candidate = "\n".join((*prefix, *candidate_rows, *suffix, closing))
        if len(candidate) > max_length:
            break
        rendered_rows.append(row)

    omitted = len(roles) - len(rendered_rows)
    suffix = [f"… | +{omitted} role khác |"] if omitted else []
    table = "\n".join((*prefix, *rendered_rows, *suffix, closing))
    if len(table) <= max_length:
        return table

    fallback = f"{len(roles)} role; bảng vượt giới hạn hiển thị."
    return fallback[:max_length]


def format_frozen_role_preview(
    frozen_roles: tuple[tuple[int, str], ...],
) -> str:
    if not frozen_roles:
        return "Không có role đủ điều kiện."

    role_rows = []
    for role_id, display in frozen_roles:
        suffix = f" (`{role_id}`)"
        role_name = display[: -len(suffix)] if display.endswith(suffix) else display
        role_rows.append((role_id, role_name))
    return format_role_table(tuple(role_rows), max_length=1024)


async def submit_role_change(
    interaction: discord.Interaction | PrefixModerationContext,
    request: RoleChangeRequest,
) -> ActionResult:
    guild = interaction.guild
    action = "gỡ" if request.remove else "gán"
    command_name = "roleunroll" if request.remove else "roleroll"
    if guild is None:
        return ActionResult(False, "Bảng chọn role chỉ dùng được trong server.")

    moderator = interaction.user
    target = guild.get_member(request.target_id)
    if target is None:
        return ActionResult(
            False,
            f"Thành viên cần {action} role không còn ở trong server.",
        )

    denial = role_target_denial(guild, moderator, target)
    if denial is not None:
        return ActionResult(False, denial)

    role = guild.get_role(request.role_id)
    if role is None:
        return ActionResult(False, "Role đã chọn không còn tồn tại.")

    change_denial = (
        role_removal_denial(guild, moderator, target, role)
        if request.remove
        else role_assignment_denial(guild, moderator, target, role)
    )
    if change_denial is not None:
        return ActionResult(False, change_denial)

    key = (guild.id, request.target_id)
    if key in ACTIVE_ROLE_MUTATION_TARGETS:
        return ActionResult(
            False,
            "Một thao tác role khác cho thành viên này đang chạy.",
        )
    ACTIVE_ROLE_MUTATION_TARGETS.add(key)
    try:
        reason = format_audit_reason(request.reason, moderator)
        if request.remove:
            await target.remove_roles(role, reason=reason)
        else:
            await target.add_roles(role, reason=reason)
    except discord.NotFound:
        return ActionResult(False, "Thành viên hoặc role không còn tồn tại.")
    except discord.Forbidden:
        return ActionResult(
            False,
            (
                f"Bot không thể {action} role này. "
                "Hãy kiểm tra quyền và thứ bậc role."
            ),
        )
    except discord.HTTPException:
        logger.exception(
            "Discord rejected %s target=%s role=%s moderator=%s",
            command_name,
            target.id,
            role.id,
            moderator.id,
        )
        return ActionResult(
            False,
            "Discord từ chối cập nhật role. Vui lòng thử lại.",
        )
    finally:
        ACTIVE_ROLE_MUTATION_TARGETS.discard(key)

    success = (
        f"Đã gỡ {role.mention} khỏi {target.mention} thành công!"
        if request.remove
        else f"Đã gán {role.mention} cho {target.mention} thành công!"
    )
    return ActionResult(True, success)


class RoleChangeView(ConfigurableModerationView):
    def __init__(
        self,
        *,
        author_id: int,
        target: discord.Member,
        remove: bool = False,
        submitter=submit_role_change,
        initial_reason: str | None = None,
    ) -> None:
        self.remove = remove
        action = "gỡ" if remove else "gán"
        namespace = "roleunroll" if remove else "roleroll"
        spec = WorkflowSpec(
            namespace=namespace,
            title="Gỡ role" if remove else "Gán role",
            action_text=f"{action} role",
            confirm_label=f"Có, {action} role",
            fields=(
                RoleField(
                    "role_id",
                    "Role",
                    placeholder=f"Chọn role muốn {action}",
                ),
            ),
            reason=(
                ROLE_REMOVE_REASON_CONFIG if remove else ROLE_ASSIGN_REASON_CONFIG
            ),
            icon="🎭",
            confirm_style=(
                discord.ButtonStyle.danger
                if remove
                else discord.ButtonStyle.success
            ),
        )

        def request_builder(
            values: dict[str, FormAnswer],
            reason: str | None,
        ) -> RoleChangeRequest:
            return RoleChangeRequest(
                target_id=target.id,
                role_id=int(values["role_id"].value),
                remove=remove,
                reason=clean_case_reason(reason),
            )

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            live_target = guild.get_member(target.id)
            if live_target is None:
                return f"Thành viên cần {action} role không còn ở trong server."
            return role_target_denial(guild, moderator, live_target)

        super().__init__(
            spec=spec,
            author_id=author_id,
            guild_id=target.guild.id,
            target=WorkflowTarget(target.id, str(target)),
            submitter=submitter,
            request_builder=request_builder,
            live_permission_check=live_permission_check,
            initial_reason=initial_reason,
        )
        self.role_select = next(
            item
            for item in self.children
            if isinstance(item, discord.ui.RoleSelect)
        )

    async def _stage_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        *,
        remove: bool,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Bảng chọn role chỉ dùng được trong server.",
                ephemeral=True,
            )
            return
        if self.target is None:
            await interaction.response.send_message(
                "Bảng chọn role không còn mục tiêu hợp lệ.",
                ephemeral=True,
            )
            return

        live_target = guild.get_member(self.target.id)
        if live_target is None:
            await interaction.response.send_message(
                "Thành viên cần xử lý role không còn ở trong server.",
                ephemeral=True,
            )
            return

        denial = (
            role_removal_denial(guild, interaction.user, live_target, role)
            if remove
            else role_assignment_denial(guild, interaction.user, live_target, role)
        )
        if denial is not None:
            await interaction.response.send_message(
                denial,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        await self.accept_answer(
            interaction,
            "role_id",
            FormAnswer(role.id, f"{role.name} (`{role.id}`)"),
        )

    async def assign_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        await self._stage_role(interaction, role, remove=False)

    async def remove_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
    ) -> None:
        await self._stage_role(interaction, role, remove=True)


class RoleRollView(RoleChangeView):
    def __init__(
        self,
        *,
        author_id: int,
        target: discord.Member,
        submitter=submit_role_change,
        initial_reason: str | None = None,
    ) -> None:
        super().__init__(
            author_id=author_id,
            target=target,
            remove=False,
            submitter=submitter,
            initial_reason=initial_reason,
        )


class RoleUnrollView(RoleChangeView):
    def __init__(
        self,
        *,
        author_id: int,
        target: discord.Member,
        submitter=submit_role_change,
        initial_reason: str | None = None,
    ) -> None:
        super().__init__(
            author_id=author_id,
            target=target,
            remove=True,
            submitter=submitter,
            initial_reason=initial_reason,
        )


class RoleCopyWorkflowView(ConfigurableModerationView):
    def __init__(
        self,
        *,
        author_id: int,
        target: discord.Member,
        submitter,
        source: discord.Member | None = None,
        plan: RoleCopyPlan | None = None,
        initial_reason: str | None = None,
    ) -> None:
        self._frozen_source_id: int | None = (
            source.id if source is not None else None
        )
        self._frozen_source_name: str | None = (
            str(source) if source is not None else None
        )
        self._source_selected_in_form = source is None
        self._frozen_roles: tuple[tuple[int, str], ...] = ()
        if plan is not None:
            self._frozen_roles = tuple(
                (role.id, f"{role.name} (`{role.id}`)") for role in plan.eligible
            )

        fields = ()
        if source is None:
            fields = (
                UserField(
                    "source_id",
                    "Nguồn sao chép",
                    placeholder="Chọn thành viên nguồn",
                ),
            )

        spec = WorkflowSpec(
            namespace="rolecopy",
            title="Sao chép role",
            action_text="sao chép role",
            confirm_label="Có, sao chép role",
            fields=fields,
            reason=ROLE_COPY_REASON_CONFIG,
            icon="📋",
            confirm_style=discord.ButtonStyle.success,
        )

        def request_builder(
            values: dict[str, FormAnswer],
            reason: str | None,
        ) -> RoleCopyRequest:
            source_id = self._frozen_source_id
            if source_id is None and "source_id" in values:
                source_id = int(values["source_id"].value)
            if source_id is None:
                raise ValueError("Chưa chọn thành viên nguồn.")
            return RoleCopyRequest(
                source_id=source_id,
                target_id=target.id,
                role_ids=tuple(role_id for role_id, _ in self._frozen_roles),
                reason=clean_case_reason(reason),
            )

        def live_permission_check(
            guild: discord.Guild,
            moderator: discord.Member,
        ) -> str | None:
            live_target = guild.get_member(target.id)
            if live_target is None:
                return "Thành viên đích không còn ở trong server."
            return role_target_denial(guild, moderator, live_target)

        super().__init__(
            spec=spec,
            author_id=author_id,
            guild_id=target.guild.id,
            target=WorkflowTarget(target.id, str(target)),
            submitter=submitter,
            request_builder=request_builder,
            live_permission_check=live_permission_check,
            initial_reason=initial_reason,
        )

    def build_embed(self) -> discord.Embed:
        embed = super().build_embed()
        if (
            self._frozen_source_id is not None
            and self._frozen_source_name is not None
            and self.step in {"reason", "confirm"}
            and not (self._source_selected_in_form and self.step == "confirm")
        ):
            embed.add_field(
                name="Nguồn sao chép",
                value=(
                    f"{safe_ui_text(self._frozen_source_name, max_length=100)} "
                    f"(`{self._frozen_source_id}`)"
                ),
                inline=False,
            )
        if self._frozen_roles and self.step in {"reason", "confirm"}:
            embed.add_field(
                name=f"Role sẽ sao chép ({len(self._frozen_roles)})",
                value=format_frozen_role_preview(self._frozen_roles),
                inline=False,
            )
        return embed

    async def _accept_answer_unlocked(
        self,
        interaction: discord.Interaction,
        key: str,
        answer: FormAnswer,
    ) -> None:
        if key == "source_id":
            if self.target is not None and answer.value == self.target.id:
                await interaction.response.send_message(
                    "Member nguồn và member đích phải khác nhau.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            guild = interaction.guild
            if guild is None:
                await interaction.response.send_message(
                    "Bảng sao chép role chỉ dùng được trong server.",
                    ephemeral=True,
                )
                return
            source = guild.get_member(int(answer.value))
            if source is None:
                await interaction.response.send_message(
                    "Thành viên nguồn không còn ở trong server.",
                    ephemeral=True,
                )
                return
            if self.target is None:
                await interaction.response.send_message(
                    "Thành viên đích không còn ở trong server.",
                    ephemeral=True,
                )
                return
            live_target = guild.get_member(self.target.id)
            if live_target is None:
                await interaction.response.send_message(
                    "Thành viên đích không còn ở trong server.",
                    ephemeral=True,
                )
                return

            plan = plan_role_copy(guild, interaction.user, source, live_target)
            if not plan.eligible:
                await interaction.response.send_message(
                    format_role_copy_result(
                        source,
                        live_target,
                        copied=[],
                        failed=[],
                        not_attempted=[],
                        already_present=len(plan.already_present),
                        unmanageable=len(plan.unmanageable),
                    ),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            self._frozen_source_id = source.id
            self._frozen_source_name = str(source)
            self._frozen_roles = tuple(
                (role.id, f"{role.name} (`{role.id}`)") for role in plan.eligible
            )
            answer = FormAnswer(source.id, f"{source} (`{source.id}`)")

        await super()._accept_answer_unlocked(interaction, key, answer)


class RollCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._role_copy_locks: dict[int, asyncio.Lock] = {}

    async def _submit_role_change(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: RoleChangeRequest,
    ) -> ActionResult:
        return await submit_role_change(interaction, request)

    async def _submit_role_copy(
        self,
        interaction: discord.Interaction | PrefixModerationContext,
        request: RoleCopyRequest,
    ) -> ActionResult:
        guild = interaction.guild
        if guild is None:
            return ActionResult(False, "Lệnh rolecopy chỉ dùng được trong server.")

        moderator = interaction.user
        permission_denial = role_permission_denial(guild, moderator)
        if permission_denial is not None:
            return ActionResult(False, permission_denial)

        source = guild.get_member(request.source_id)
        target = guild.get_member(request.target_id)
        if source is None or target is None:
            return ActionResult(
                False,
                "Không còn dữ liệu thành viên để sao chép role.",
            )
        if source.id == target.id:
            return ActionResult(
                False,
                "Member nguồn và member đích phải khác nhau.",
            )

        target_denial = role_target_denial(guild, moderator, target)
        if target_denial is not None:
            return ActionResult(False, target_denial)

        lock = self._role_copy_locks.setdefault(guild.id, asyncio.Lock())
        if lock.locked():
            return ActionResult(
                False,
                "Một lệnh rolecopy khác đang được xử lý trong server. Hãy thử lại sau.",
            )

        async with lock:
            return await self._copy_frozen_roles(
                guild,
                moderator,
                source,
                target,
                request,
            )

    async def _copy_frozen_roles(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        source: discord.Member,
        target: discord.Member,
        request: RoleCopyRequest,
    ) -> ActionResult:
        copied: list[discord.Role] = []
        failed: list[discord.Role] = []
        not_attempted: list[discord.Role] = []
        source_changed = 0
        unmanageable = 0
        already_present = 0
        stop_reason: str | None = None
        remaining = [
            guild.get_role(role_id) for role_id in request.role_ids
        ]
        audit_reason = format_audit_reason(
            (
                f"rolecopy source={source.id} target={target.id} · "
                f"{request.reason}"
            ),
            moderator,
        )
        mutation_key = (guild.id, target.id)
        if mutation_key in ACTIVE_ROLE_MUTATION_TARGETS:
            return ActionResult(
                False,
                "Một thao tác role khác cho thành viên này đang chạy.",
            )
        ACTIVE_ROLE_MUTATION_TARGETS.add(mutation_key)
        try:
            for index, role in enumerate(remaining):
                later = [item for item in remaining[index + 1 :] if item is not None]
                if role is None or role not in source.roles:
                    source_changed += 1
                    continue
                manageability = _role_manageability_denial(
                    guild,
                    moderator,
                    role,
                    action="gán",
                )
                if manageability is not None:
                    unmanageable += 1
                    continue
                if role in target.roles:
                    already_present += 1
                    continue
                try:
                    await target.add_roles(role, reason=audit_reason)
                except discord.NotFound:
                    failed.append(role)
                    not_attempted.extend(later)
                    stop_reason = (
                        "Đã dừng vì member hoặc role không còn tồn tại trong server."
                    )
                    logger.warning(
                        "rolecopy resource missing source=%s target=%s role=%s moderator=%s",
                        source.id,
                        target.id,
                        role.id,
                        moderator.id,
                    )
                    break
                except discord.Forbidden:
                    failed.append(role)
                    not_attempted.extend(later)
                    stop_reason = (
                        "Đã dừng vì bot không còn đủ quyền hoặc "
                        "thứ bậc role đã thay đổi."
                    )
                    logger.warning(
                        "rolecopy forbidden source=%s target=%s role=%s moderator=%s",
                        source.id,
                        target.id,
                        role.id,
                        moderator.id,
                    )
                    break
                except discord.HTTPException:
                    failed.append(role)
                    not_attempted.extend(later)
                    stop_reason = "Đã dừng vì Discord từ chối cập nhật role."
                    logger.exception(
                        "rolecopy failed source=%s target=%s role=%s moderator=%s",
                        source.id,
                        target.id,
                        role.id,
                        moderator.id,
                    )
                    break
                else:
                    copied.append(role)
        finally:
            ACTIVE_ROLE_MUTATION_TARGETS.discard(mutation_key)

        return ActionResult(
            True,
            format_role_copy_result(
                source,
                target,
                copied=copied,
                failed=failed,
                not_attempted=not_attempted,
                source_changed=source_changed,
                unmanageable=unmanageable,
                already_present=already_present,
                stop_reason=stop_reason,
                reason=request.reason,
            ),
        )

    async def _resolve_optional_member(
        self,
        ctx: commands.Context,
        member: discord.Member | None,
        extra_present: bool,
        *,
        command_name: str,
    ) -> discord.Member | None:
        if ctx.message.reference is not None:
            if member is not None or extra_present:
                await ctx.reply(
                    (
                        f"Khi dùng {command_name} bằng reply, chỉ dùng lệnh "
                        f"`{ctx.clean_prefix}{command_name}` không kèm member "
                        "hoặc lý do; bạn sẽ chọn trong bảng."
                    ),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return None
            try:
                return await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return None
        if member is None:
            await ctx.reply(
                (
                    f"Hãy mention thành viên bằng `{ctx.clean_prefix}{command_name} @user` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}{command_name}`."
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return None
        return member

    async def _open_role_change(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        remove: bool,
        reason: str | None,
    ) -> None:
        denial = role_target_denial(ctx.guild, ctx.author, member)
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        view_cls = RoleUnrollView if remove else RoleRollView
        view = view_cls(
            author_id=ctx.author.id,
            target=member,
            submitter=self._submit_role_change,
            initial_reason=reason,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _run_role_change(
        self,
        ctx: commands.Context,
        member: discord.Member,
        role_name: str,
        *,
        remove: bool,
    ) -> None:
        value = role_name.strip()
        identifier = (
            value[3:-1]
            if value.startswith("<@&") and value.endswith(">")
            else value
        )
        role = ctx.guild.get_role(int(identifier)) if identifier.isdecimal() else None
        if role is None:
            role = next(
                (role for role in ctx.guild.roles if role.name.lower() == value.lower()),
                None,
            )
        if role is None:
            await ctx.reply(
                f"Không tìm thấy role `{safe_ui_text(value)}`.",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await run_prefix_action(
            ctx,
            self._submit_role_change,
            RoleChangeRequest(member.id, role.id, remove, clean_case_reason(None)),
        )

    async def _handle_role_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
        *,
        command_name: str,
        remove: bool,
    ) -> None:
        action = "gỡ" if remove else "gán"
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                f"Bạn không có quyền Manage Roles để {action} role.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                (
                    f"Cách dùng: `{ctx.clean_prefix}{command_name} @user` "
                    f"hoặc reply tin nhắn bằng `{ctx.clean_prefix}{command_name}`."
                ),
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy thành viên đó trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Hãy mention một thành viên hợp lệ trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                f"Lệnh {command_name} chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                (
                    f"Hãy thử {command_name} lại sau "
                    f"{error.retry_after:.1f} giây."
                ),
                mention_author=False,
            )
            return
        raise error

    @commands.command(
        name="roleroll",
        help="Gán role theo tên/ID/mention; thiếu role để mở bảng chọn.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.cooldown(
        1,
        ROLE_CHANGE_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def give_role(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        role_name: str | None = None,
    ) -> None:
        resolved = await self._resolve_optional_member(
            ctx,
            member,
            role_name is not None,
            command_name="roleroll",
        )
        if resolved is None:
            return
        if role_name is not None:
            await self._run_role_change(ctx, resolved, role_name, remove=False)
            return
        await self._open_role_change(
            ctx,
            resolved,
            remove=False,
            reason=None,
        )

    @give_role.error
    async def give_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        await self._handle_role_command_error(
            ctx,
            error,
            command_name="roleroll",
            remove=False,
        )

    @commands.command(
        name="roleunroll",
        help="Gỡ role theo tên/ID/mention; thiếu role để mở bảng chọn.",
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.cooldown(
        1,
        ROLE_CHANGE_COOLDOWN_SECONDS,
        commands.BucketType.member,
    )
    async def remove_role(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        role_name: str | None = None,
    ) -> None:
        resolved = await self._resolve_optional_member(
            ctx,
            member,
            role_name is not None,
            command_name="roleunroll",
        )
        if resolved is None:
            return
        if role_name is not None:
            await self._run_role_change(ctx, resolved, role_name, remove=True)
            return
        await self._open_role_change(
            ctx,
            resolved,
            remove=True,
            reason=None,
        )

    @remove_role.error
    async def remove_role_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        await self._handle_role_command_error(
            ctx,
            error,
            command_name="roleunroll",
            remove=True,
        )

    @commands.command(
        name="rolecopy",
        help=(
            "Sao chép role khi nhập đủ nguồn và đích; "
            "reply không đối số để mở bảng chọn nguồn."
        ),
        cooldown_after_parsing=True,
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_roles=True)
    @commands.max_concurrency(
        1,
        per=commands.BucketType.guild,
        wait=False,
    )
    @commands.cooldown(
        1,
        ROLE_COPY_COOLDOWN_SECONDS,
        commands.BucketType.user,
    )
    async def copy_roles(
        self,
        ctx: commands.Context,
        source: discord.Member | None = None,
        target: discord.Member | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        if ctx.message.reference is not None:
            if source is not None or target is not None or reason is not None:
                await ctx.reply(
                    (
                        "Khi rolecopy bằng reply, chỉ dùng lệnh "
                        f"`{ctx.clean_prefix}rolecopy` không kèm member hoặc lý do; "
                        "reply là đích và bạn sẽ chọn nguồn trong bảng."
                    ),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            try:
                target = await resolve_same_channel_reply_member(ctx)
            except ReplyTargetError as exc:
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            denial = role_target_denial(ctx.guild, ctx.author, target)
            if denial is not None:
                await ctx.reply(
                    denial,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            view = RoleCopyWorkflowView(
                author_id=ctx.author.id,
                target=target,
                submitter=self._submit_role_copy,
            )
            view.message = await ctx.reply(
                embed=view.build_embed(),
                view=view,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if source is None or target is None:
            await ctx.reply(
                (
                    f"Cách dùng: `{ctx.clean_prefix}rolecopy @source @target` "
                    f"hoặc reply tin nhắn đích bằng `{ctx.clean_prefix}rolecopy`."
                ),
                mention_author=False,
            )
            return
        if source.id == target.id:
            await ctx.reply(
                "Member nguồn và member đích phải khác nhau.",
                mention_author=False,
            )
            return

        denial = role_target_denial(ctx.guild, ctx.author, target)
        if denial is not None:
            await ctx.reply(
                denial,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        plan = plan_role_copy(ctx.guild, ctx.author, source, target)
        if not plan.eligible:
            await ctx.reply(
                format_role_copy_result(
                    source,
                    target,
                    copied=[],
                    failed=[],
                    not_attempted=[],
                    already_present=len(plan.already_present),
                    unmanageable=len(plan.unmanageable),
                ),
                allowed_mentions=discord.AllowedMentions.none(),
                mention_author=False,
            )
            return

        await run_prefix_action(
            ctx,
            self._submit_role_copy,
            RoleCopyRequest(
                source.id,
                target.id,
                tuple(role.id for role in plan.eligible),
                clean_case_reason(reason),
            ),
        )

    @copy_roles.error
    async def copy_roles_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                "Bạn không có quyền Manage Roles để sao chép role.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                (
                    f"Cách dùng: `{ctx.clean_prefix}rolecopy @source @target` "
                    f"hoặc reply tin nhắn đích bằng `{ctx.clean_prefix}rolecopy`."
                ),
                mention_author=False,
            )
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Mình không tìm thấy một trong hai thành viên trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                "Hãy mention member nguồn và member đích hợp lệ.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.reply(
                "Lệnh rolecopy chỉ dùng được trong server.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Hãy thử lại rolecopy sau {error.retry_after:.1f} giây.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MaxConcurrencyReached):
            await ctx.reply(
                "Một lệnh rolecopy khác đang chạy trong server. Hãy thử lại sau.",
                mention_author=False,
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollCog(bot))
