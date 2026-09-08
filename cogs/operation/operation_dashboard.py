import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import logging
import math
import time
from typing import Any

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.operation._doctor import collect_doctor_checks
from cogs.operation._operation_helpers import (
    AUDIT_RANGE_LABELS,
    AUDIT_TIME_RANGES,
    MAX_EXPORT_ROWS,
    PRUNE_RANGE_LABELS,
    PRUNE_TIME_RANGES,
    AuditExportError,
    ExportRowLimitError,
    TimeRangeOption,
    classify_command_error,
    command_error_type,
    get_audit_cutoff,
    get_prune_cutoff,
    sanitize_command_arguments,
    split_audit_csv,
)
from cogs.operation._setup_helpers import SetupCheck, summarize_checks


logger = logging.getLogger(__name__)

LOG_COLLECTION = "operation_logs"
DASHBOARD_TIMEOUT_SECONDS = 180
DOCTOR_PAGE_SIZE = 5
JOINED_SERVER_PAGE_SIZE = 10
AUDIT_PAGE_SIZE = 5
AUDIT_BROWSER_MAX_ROWS = 1_000
DEFAULT_AUDIT_RANGE = "30d"
DEFAULT_EXPORT_RANGE = "all"
DEFAULT_PRUNE_RANGE = "30d"
DEFAULT_UPLOAD_LIMIT = 8 * 1024 * 1024
UPLOAD_SIZE_RESERVE = 64 * 1024
INDEX_RETRY_SECONDS = 60
NO_MENTIONS = discord.AllowedMentions.none()

STATUS_LABELS = {
    "running": "Đang chạy",
    "succeeded": "Thành công",
    "denied": "Bị từ chối",
    "invalid": "Sai tham số",
    "cooldown": "Cooldown",
    "failed": "Lỗi",
}
STATUS_ICONS = {
    "running": "⏳",
    "succeeded": "✅",
    "denied": "⛔",
    "invalid": "⚠️",
    "cooldown": "🕒",
    "failed": "❌",
}

LIFECYCLE_EVENT_LABELS = {
    "initial_ready": "Ready đầu tiên",
    "reidentified": "Định danh lại",
    "resumed": "Khôi phục phiên",
}


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    generated_at: datetime
    started_at: datetime
    ready: bool
    latency_ms: int | None
    environment: str
    connected_guilds: int
    cached_members: int
    guild_members: int
    guild_channels: int
    mongo_available: bool
    mongo_latency_ms: int | None
    retained_logs: int | None
    recent_statuses: dict[str, int]


async def _send_private(
    interaction: discord.Interaction,
    content: str,
) -> None:
    kwargs = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": NO_MENTIONS,
    }
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def _disable_view(view: discord.ui.View, disabled: bool = True) -> None:
    for item in view.children:
        item.disabled = disabled


def _format_uptime(started_at: datetime, now: datetime) -> str:
    seconds = max(0, int((now - started_at).total_seconds()))
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)
    return f"{days} ngày, {hours} giờ, {minutes} phút"


def _safe_display(value: object, limit: int) -> str:
    text = discord.utils.escape_markdown(str(value or ""))
    text = text.replace("`", "ˋ")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _component_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "Không tên").split()) or "Không tên"
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


async def _check_owner_access(
    interaction: discord.Interaction,
    *,
    bot: commands.Bot,
    source_guild_id: int,
    author_id: int,
) -> bool:
    guild = interaction.guild
    if guild is None or guild.id != source_guild_id:
        await _send_private(
            interaction,
            "Bảng quản lý này chỉ dùng được trong server đã mở nó.",
        )
        return False
    if interaction.user.id != author_id:
        await _send_private(
            interaction,
            "Bảng riêng này chỉ dành cho bot owner đã mở nó.",
        )
        return False
    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions is None or not permissions.administrator:
        await _send_private(
            interaction,
            "Bạn cần quyền Administrator hiện tại để dùng bảng này.",
        )
        return False
    try:
        is_owner = await bot.is_owner(interaction.user)
    except Exception:
        logger.exception(
            "Failed to recheck bot owner source_guild=%s actor=%s",
            source_guild_id,
            interaction.user.id,
        )
        await _send_private(
            interaction,
            "Không thể xác minh bot owner lúc này. Vui lòng thử lại.",
        )
        return False
    if not is_owner:
        await _send_private(
            interaction,
            "Chỉ bot owner hiện tại mới dùng được bảng quản lý này.",
        )
        return False
    return True


class GuildAdminView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        author_id: int | None = None,
        timeout: float = DASHBOARD_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.author_id = author_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            await _send_private(
                interaction,
                "Bảng vận hành này chỉ dùng được trong server đã mở nó.",
            )
            return False
        if self.author_id is not None and interaction.user.id != self.author_id:
            await _send_private(
                interaction,
                "Bảng riêng này chỉ dành cho Administrator đã mở nó.",
            )
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions is None or not permissions.administrator:
            await _send_private(
                interaction,
                "Bạn cần quyền Administrator hiện tại để dùng bảng này.",
            )
            return False
        return True

    async def on_timeout(self) -> None:
        _disable_view(self)
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.error(
            "Operation dashboard interaction failed guild=%s item=%s",
            self.guild_id,
            getattr(item, "custom_id", type(item).__name__),
            exc_info=(type(error), error, error.__traceback__),
        )
        try:
            await _send_private(
                interaction,
                "Không thể hoàn tất thao tác vận hành. Vui lòng thử lại.",
            )
        except discord.HTTPException:
            pass


class TimeRangeSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        options: Sequence[TimeRangeOption],
        selected_key: str,
        placeholder: str,
        callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        self._range_callback = callback
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=option.label,
                    value=option.key,
                    default=option.key == selected_key,
                )
                for option in options
            ],
            row=0,
        )

    def set_selected(self, selected_key: str) -> None:
        for option in self.options:
            option.default = option.value == selected_key

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._range_callback(interaction, self.values[0])


class AuditLogView(GuildAdminView):
    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(guild_id=guild_id, author_id=author_id)
        self.cog = cog
        self.range_key = DEFAULT_AUDIT_RANGE
        self.page = 0
        self.documents: list[dict[str, Any]] = []
        self.total = 0
        self._navigation_lock = asyncio.Lock()
        self.range_select = TimeRangeSelect(
            options=AUDIT_TIME_RANGES,
            selected_key=self.range_key,
            placeholder="Chọn thời gian audit",
            callback=self._change_range,
        )
        self.add_item(self.range_select)

    async def load_page(self) -> None:
        offset = self.page * AUDIT_PAGE_SIZE
        self.documents, self.total = await self.cog.fetch_audit_page(
            guild_id=self.guild_id,
            range_key=self.range_key,
            offset=offset,
            limit=AUDIT_PAGE_SIZE,
        )
        available = min(self.total, AUDIT_BROWSER_MAX_ROWS)
        last_page = max(0, (available - 1) // AUDIT_PAGE_SIZE)
        if self.page > last_page:
            self.page = last_page
            offset = self.page * AUDIT_PAGE_SIZE
            self.documents, self.total = await self.cog.fetch_audit_page(
                guild_id=self.guild_id,
                range_key=self.range_key,
                offset=offset,
                limit=AUDIT_PAGE_SIZE,
            )
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = (
            not self.documents
            or (self.page + 1) * AUDIT_PAGE_SIZE >= available
        )

    def build_embed(self) -> discord.Embed:
        range_label = AUDIT_RANGE_LABELS[self.range_key]
        embed = discord.Embed(
            title="📋 Audit command",
            description=(
                f"Phạm vi: **{range_label}** · {self.total:,} bản ghi. "
                f"Hiển thị tối đa {AUDIT_BROWSER_MAX_ROWS:,} bản ghi mới nhất."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        if not self.documents:
            embed.add_field(
                name="Không có dữ liệu",
                value="Chưa có command hoặc thao tác vận hành trong phạm vi này.",
                inline=False,
            )
        for document in self.documents:
            status = str(document.get("status", "failed"))
            icon = STATUS_ICONS.get(status, "•")
            label = STATUS_LABELS.get(status, status)
            name = document.get("command_name") or document.get("action") or "unknown"
            created_at = document.get("created_at")
            if isinstance(created_at, datetime):
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                timestamp = f"<t:{int(created_at.timestamp())}:R>"
            else:
                timestamp = "không rõ thời gian"
            actor_name = _safe_display(document.get("actor_name"), 80)
            actor_id = document.get("actor_id", "?")
            channel_id = document.get("channel_id", "?")
            arguments = _safe_display(document.get("arguments"), 220)
            details = document.get("details")
            if details:
                details_text = _safe_display(
                    json.dumps(details, ensure_ascii=False, default=str),
                    180,
                )
            else:
                details_text = ""
            value_lines = [
                f"**Trạng thái:** {label} · {timestamp}",
                f"**Người dùng:** {actor_name} (`{actor_id}`)",
                f"**Channel:** `{channel_id}`",
            ]
            if arguments:
                value_lines.append(f"**Đối số:** {arguments}")
            if details_text:
                value_lines.append(f"**Chi tiết:** {details_text}")
            error_type = document.get("error_type")
            if error_type:
                value_lines.append(f"**Loại lỗi:** `{_safe_display(error_type, 80)}`")
            embed.add_field(
                name=f"{icon} {_safe_display(name, 120)}",
                value="\n".join(value_lines)[:1024],
                inline=False,
            )
        available = min(self.total, AUDIT_BROWSER_MAX_ROWS)
        page_count = max(1, math.ceil(available / AUDIT_PAGE_SIZE))
        embed.set_footer(text=f"Trang {self.page + 1}/{page_count}")
        return embed

    async def _change_range(
        self,
        interaction: discord.Interaction,
        range_key: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            previous_range = self.range_key
            previous_page = self.page
            self.range_key = range_key
            self.page = 0
            self.range_select.set_selected(range_key)
            try:
                await self.load_page()
            except PyMongoError:
                self.range_key = previous_range
                self.page = previous_page
                self.range_select.set_selected(previous_range)
                logger.exception(
                    "Failed to change audit range guild=%s",
                    self.guild_id,
                )
                await interaction.followup.send(
                    "Không thể đọc audit log từ MongoDB.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Trước",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            if self.page > 0:
                self.page -= 1
            await self.load_page()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Sau",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            available = min(self.total, AUDIT_BROWSER_MAX_ROWS)
            if (self.page + 1) * AUDIT_PAGE_SIZE < available:
                self.page += 1
            await self.load_page()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Đóng",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            _disable_view(self)
            self.stop()
            await interaction.edit_original_response(
                content="Đã đóng bảng audit.",
                embed=None,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )


class ExportLogView(GuildAdminView):
    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(guild_id=guild_id, author_id=author_id)
        self.cog = cog
        self.range_key = DEFAULT_EXPORT_RANGE
        self.completed = False
        self._action_lock = asyncio.Lock()
        self.range_select = TimeRangeSelect(
            options=AUDIT_TIME_RANGES,
            selected_key=self.range_key,
            placeholder="Chọn thời gian xuất CSV",
            callback=self._change_range,
        )
        self.add_item(self.range_select)

    def build_embed(self, *, processing: bool = False) -> discord.Embed:
        label = AUDIT_RANGE_LABELS[self.range_key]
        description = (
            "Đang tạo CSV và chia file theo giới hạn upload của server…"
            if processing
            else (
                f"Phạm vi đã chọn: **{label}**. Tối đa "
                f"{MAX_EXPORT_ROWS:,} bản ghi; dữ liệu được sắp xếp mới nhất trước."
            )
        )
        return discord.Embed(
            title="📥 Tải audit log CSV",
            description=description,
            color=discord.Color.green(),
        )

    async def _change_range(
        self,
        interaction: discord.Interaction,
        range_key: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "CSV của thao tác này đã được tạo.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            self.range_key = range_key
            self.range_select.set_selected(range_key)
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    def _restore_controls(self) -> None:
        _disable_view(self, False)

    @discord.ui.button(
        label="Tải CSV",
        emoji="📥",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def download(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "CSV của thao tác này đã được tạo.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            _disable_view(self)
            await interaction.edit_original_response(
                embed=self.build_embed(processing=True),
                view=self,
            )
            documents: list[dict[str, Any]] = []
            delivered_parts = 0
            try:
                documents = await self.cog.fetch_export_documents(
                    guild_id=self.guild_id,
                    range_key=self.range_key,
                )
                if not documents:
                    self._restore_controls()
                    await interaction.edit_original_response(
                        embed=self.build_embed(),
                        view=self,
                    )
                    await interaction.followup.send(
                        "Không có audit log trong phạm vi đã chọn.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                    return

                guild_limit = int(
                    getattr(interaction.guild, "filesize_limit", DEFAULT_UPLOAD_LIMIT)
                )
                max_part_bytes = max(1_024, guild_limit - UPLOAD_SIZE_RESERVE)
                parts = await asyncio.to_thread(
                    split_audit_csv,
                    documents,
                    max_bytes=max_part_bytes,
                )
                date_stamp = discord.utils.utcnow().strftime("%Y%m%d-%H%M%S")
                part_width = max(2, len(str(len(parts))))
                for index, payload in enumerate(parts, start=1):
                    suffix = (
                        ""
                        if len(parts) == 1
                        else f"-part-{index:0{part_width}d}-of-{len(parts):0{part_width}d}"
                    )
                    filename = (
                        f"operation-logs-{self.guild_id}-{self.range_key}-"
                        f"{date_stamp}{suffix}.csv"
                    )
                    await interaction.followup.send(
                        content=f"CSV audit {index}/{len(parts)} · {len(documents):,} bản ghi",
                        file=discord.File(BytesIO(payload), filename=filename),
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                    delivered_parts += 1
            except ExportRowLimitError:
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="export_logs",
                    status="failed",
                    details={"range": self.range_key, "reason": "row_limit"},
                )
                self._restore_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    (
                        f"Phạm vi này vượt {MAX_EXPORT_ROWS:,} bản ghi. "
                        "Hãy chọn 7, 30 hoặc 90 ngày."
                    ),
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            except AuditExportError as error:
                logger.warning("Could not split audit CSV guild=%s: %s", self.guild_id, error)
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="export_logs",
                    status="failed",
                    details={"range": self.range_key, "reason": type(error).__name__},
                )
                self._restore_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Một dòng CSV vượt giới hạn upload. Hãy chọn phạm vi nhỏ hơn.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            except PyMongoError:
                logger.exception("Failed to export audit logs guild=%s", self.guild_id)
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="export_logs",
                    status="failed",
                    details={"range": self.range_key, "reason": "database"},
                )
                self._restore_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Không thể đọc audit log từ MongoDB.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            except discord.HTTPException:
                logger.exception(
                    "Failed to deliver audit CSV guild=%s delivered_parts=%s",
                    self.guild_id,
                    delivered_parts,
                )
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="export_logs",
                    status="failed",
                    details={
                        "range": self.range_key,
                        "rows": len(documents),
                        "delivered_parts": delivered_parts,
                        "reason": "discord_upload",
                    },
                )
                self._restore_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Discord không nhận đủ file CSV. Bạn có thể thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            await self.cog.record_admin_action(
                interaction=interaction,
                action="export_logs",
                status="succeeded",
                details={
                    "range": self.range_key,
                    "rows": len(documents),
                    "parts": delivered_parts,
                },
            )
            self.completed = True
            self.stop()
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="✅ Đã tạo CSV",
                    description=(
                        f"Đã gửi {len(documents):,} bản ghi trong "
                        f"{delivered_parts} file."
                    ),
                    color=discord.Color.green(),
                ),
                view=self,
            )

    @discord.ui.button(
        label="Hủy",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "CSV của thao tác này đã được tạo.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            _disable_view(self)
            self.stop()
            await interaction.edit_original_response(
                content="Đã hủy xuất CSV.",
                embed=None,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )


class PruneLogView(GuildAdminView):
    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(guild_id=guild_id, author_id=author_id)
        self.cog = cog
        self.range_key = DEFAULT_PRUNE_RANGE
        self.cutoff: datetime | None = None
        self.matching_count = 0
        self.clear_all_armed = False
        self.completed = False
        self._action_lock = asyncio.Lock()
        self.range_select = TimeRangeSelect(
            options=PRUNE_TIME_RANGES,
            selected_key=self.range_key,
            placeholder="Chọn phạm vi dọn log",
            callback=self._change_range,
        )
        self.add_item(self.range_select)

    async def refresh_preview(self) -> None:
        cutoff = get_prune_cutoff(
            self.range_key,
            now=discord.utils.utcnow(),
        )
        matching_count = await self.cog.count_prunable_logs(
            guild_id=self.guild_id,
            cutoff=cutoff,
        )
        self.cutoff = cutoff
        self.matching_count = matching_count
        self._apply_preview_controls()

    def _apply_preview_controls(self) -> None:
        self.confirm.label = (
            "Tiếp tục xóa tất cả"
            if self.range_key == "all" and not self.clear_all_armed
            else "Xác nhận xóa"
        )
        self.confirm.disabled = self.matching_count == 0

    def build_embed(self, *, processing: bool = False) -> discord.Embed:
        label = PRUNE_RANGE_LABELS[self.range_key]
        if processing:
            description = "Đang xóa các bản ghi đã xác nhận…"
        elif self.range_key == "all" and self.clear_all_armed:
            description = (
                f"**Xác nhận lần cuối:** xóa toàn bộ {self.matching_count:,} bản ghi "
                "của server này. Thao tác không thể hoàn tác."
            )
        else:
            description = (
                f"Phạm vi: **{label}** · sẽ xóa {self.matching_count:,} bản ghi. "
                "Thao tác này không thể hoàn tác."
            )
        return discord.Embed(
            title="🗑️ Dọn audit log",
            description=description,
            color=discord.Color.red(),
        )

    @staticmethod
    def build_loading_embed(range_key: str) -> discord.Embed:
        return discord.Embed(
            title="🗑️ Dọn audit log",
            description=f"Đang đếm bản ghi cho **{PRUNE_RANGE_LABELS[range_key]}**…",
            color=discord.Color.orange(),
        )

    async def _change_range(
        self,
        interaction: discord.Interaction,
        range_key: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "Thao tác dọn log này đã hoàn tất.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            _disable_view(self)
            await interaction.edit_original_response(
                embed=self.build_loading_embed(range_key),
                view=self,
            )
            cutoff = get_prune_cutoff(range_key, now=discord.utils.utcnow())
            try:
                matching_count = await self.cog.count_prunable_logs(
                    guild_id=self.guild_id,
                    cutoff=cutoff,
                )
            except PyMongoError:
                _disable_view(self, False)
                self._apply_preview_controls()
                logger.exception(
                    "Failed to preview audit prune guild=%s",
                    self.guild_id,
                )
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Không thể đếm audit log từ MongoDB; phạm vi cũ được giữ nguyên.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            self.range_key = range_key
            self.cutoff = cutoff
            self.matching_count = matching_count
            self.clear_all_armed = False
            self.range_select.set_selected(range_key)
            _disable_view(self, False)
            self._apply_preview_controls()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Xác nhận xóa",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "Thao tác dọn log này đã hoàn tất.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if self.range_key == "all" and not self.clear_all_armed:
                self.clear_all_armed = True
                button.label = "Xác nhận xóa toàn bộ"
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                return
            _disable_view(self)
            await interaction.edit_original_response(
                embed=self.build_embed(processing=True),
                view=self,
            )
            try:
                deleted_count = await self.cog.prune_logs(
                    guild_id=self.guild_id,
                    cutoff=self.cutoff,
                )
            except PyMongoError:
                logger.exception("Failed to prune audit logs guild=%s", self.guild_id)
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="prune_logs",
                    status="failed",
                    details={
                        "range": self.range_key,
                        "cutoff": self.cutoff,
                        "reason": "database",
                    },
                )
                self.clear_all_armed = False
                await self.refresh_preview_after_failure()
                _disable_view(self, False)
                self._apply_preview_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Không thể xác nhận thao tác xóa với MongoDB. Bạn có thể thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            action_recorded = await self.cog.record_admin_action(
                interaction=interaction,
                action="prune_logs",
                status="succeeded",
                details={
                    "range": self.range_key,
                    "cutoff": self.cutoff,
                    "deleted_count": deleted_count,
                },
            )
            self.completed = True
            self.stop()
            audit_note = (
                "Bản ghi của thao tác dọn log này đã được thêm sau khi xóa."
                if action_recorded
                else (
                    "Không thể ghi lại thao tác dọn log sau khi xóa; "
                    "hãy kiểm tra kết nối MongoDB."
                )
            )
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="✅ Đã dọn audit log",
                    description=(
                        f"Đã xóa {deleted_count:,} bản ghi. {audit_note}"
                    ),
                    color=discord.Color.green(),
                ),
                view=self,
            )

    async def refresh_preview_after_failure(self) -> None:
        try:
            await self.refresh_preview()
        except PyMongoError:
            self.matching_count = 0
            self._apply_preview_controls()

    @discord.ui.button(
        label="Hủy",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                await interaction.followup.send(
                    "Thao tác dọn log này đã hoàn tất.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            _disable_view(self)
            self.stop()
            await interaction.edit_original_response(
                content="Đã hủy dọn audit log.",
                embed=None,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )


class BotOwnerGuildAdminView(GuildAdminView):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(guild_id=guild_id, author_id=author_id)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _check_owner_access(
            interaction,
            bot=self.bot,
            source_guild_id=self.guild_id,
            author_id=int(self.author_id),
        )


class LifecycleHistoryView(BotOwnerGuildAdminView):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(bot=bot, guild_id=guild_id, author_id=author_id)
        self.documents: list[dict[str, Any]] = []
        self.mongo_available = True
        self._refresh_lock = asyncio.Lock()

    async def load_events(self) -> None:
        recorder = getattr(self.bot, "lifecycle_recorder", None)
        if recorder is None:
            self.documents = []
            self.mongo_available = False
            return
        try:
            documents, mongo_available = await recorder.fetch_recent(limit=10)
        except PyMongoError:
            logger.exception("Failed to fetch bot lifecycle history")
            self.documents = []
            self.mongo_available = False
            return
        self.documents = list(documents[:10])
        self.mongo_available = bool(mongo_available)

    def build_embed(self) -> discord.Embed:
        recorder = getattr(self.bot, "lifecycle_recorder", None)
        environment = str(
            getattr(
                recorder,
                "environment",
                getattr(self.bot, "environment", "production"),
            )
        )
        description = f"10 sự kiện kết nối mới nhất của `{_safe_display(environment, 80)}`."
        if not self.mongo_available:
            description += (
                "\n⚠️ MongoDB không khả dụng; lịch sử có thể chỉ gồm sự kiện "
                "đang được cache trong tiến trình hiện tại."
            )
        embed = discord.Embed(
            title="🔌 Lịch sử kết nối",
            description=description,
            color=(
                discord.Color.blurple()
                if self.mongo_available
                else discord.Color.orange()
            ),
            timestamp=discord.utils.utcnow(),
        )
        if not self.documents:
            embed.add_field(
                name="Chưa có dữ liệu",
                value="Bot chưa ghi nhận sự kiện Ready hoặc Resume nào.",
                inline=False,
            )
            return embed

        for document in self.documents:
            event_type = str(document.get("event_type", "unknown"))
            label = LIFECYCLE_EVENT_LABELS.get(event_type, event_type)
            occurred_at = document.get("occurred_at")
            if isinstance(occurred_at, datetime):
                occurred_at = _as_utc(occurred_at)
                occurred = f"<t:{int(occurred_at.timestamp())}:F>"
                relative = f"<t:{int(occurred_at.timestamp())}:R>"
            else:
                occurred = "Không rõ thời gian"
                relative = ""
            process_started_at = document.get("process_started_at")
            if isinstance(process_started_at, datetime):
                process_started_at = _as_utc(process_started_at)
                process_started = (
                    f"<t:{int(process_started_at.timestamp())}:F>"
                )
            else:
                process_started = "Không rõ"
            guild_count = document.get("guild_count")
            guild_count_text = (
                f"{int(guild_count):,}"
                if isinstance(guild_count, int)
                else "Không rõ"
            )
            event_id = _safe_display(document.get("_id", "?"), 100)
            embed.add_field(
                name=f"{_safe_display(label, 120)} {relative}".strip(),
                value=(
                    f"Sự kiện: {occurred}\n"
                    f"Process bắt đầu: {process_started}\n"
                    f"Server quan sát: **{guild_count_text}**\n"
                    f"ID: `{event_id}`"
                )[:1024],
                inline=False,
            )
        return embed

    @discord.ui.button(
        label="Làm mới",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._refresh_lock.locked():
            await _send_private(interaction, "Lịch sử đang được làm mới.")
            return
        await interaction.response.defer(ephemeral=True)
        async with self._refresh_lock:
            await self.load_events()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Đóng",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        _disable_view(self)
        self.stop()
        await interaction.edit_original_response(
            content="Đã đóng lịch sử kết nối.",
            embed=None,
            view=self,
            allowed_mentions=NO_MENTIONS,
        )


class JoinedServerSelect(discord.ui.Select):
    def __init__(self, manager: "JoinedServerView") -> None:
        self.manager = manager
        super().__init__(
            placeholder="Chọn server để quản lý",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="Đang tải…", value="loading")],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.manager.select_guild(interaction, self.values[0])


class JoinedServerView(BotOwnerGuildAdminView):
    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        author_id: int,
    ) -> None:
        super().__init__(bot=cog.bot, guild_id=guild_id, author_id=author_id)
        self.cog = cog
        self.guilds: list[discord.Guild] = []
        self.page = 0
        self.selected_guild_id: int | None = None
        self.leave_armed = False
        self.completed = False
        self._navigation_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self.guild_select = JoinedServerSelect(self)
        self.add_item(self.guild_select)
        self.reload_guilds()

    @staticmethod
    def _sort_key(guild: discord.Guild) -> tuple[str, int]:
        return (str(getattr(guild, "name", "")).casefold(), int(guild.id))

    @staticmethod
    def _member_count(guild: discord.Guild) -> int:
        count = getattr(guild, "member_count", None)
        if count is not None:
            return int(count)
        return len(getattr(guild, "members", ()))

    @staticmethod
    def _channel_count(guild: discord.Guild) -> int:
        return len(getattr(guild, "channels", ()))

    def reload_guilds(self) -> None:
        self.guilds = sorted(
            list(getattr(self.bot, "guilds", ())),
            key=self._sort_key,
        )
        last_page = max(0, (len(self.guilds) - 1) // JOINED_SERVER_PAGE_SIZE)
        self.page = min(self.page, last_page)
        self._sync_controls()

    def _page_guilds(self) -> list[discord.Guild]:
        start = self.page * JOINED_SERVER_PAGE_SIZE
        return self.guilds[start : start + JOINED_SERVER_PAGE_SIZE]

    def _reset_confirmation(self) -> None:
        self.leave_armed = False
        self.leave_server.label = "Rời server"
        self.leave_server.style = discord.ButtonStyle.danger

    def _resolve_leave_target(
        self,
        target_id: int,
    ) -> tuple[discord.Guild | None, str | None]:
        source_guild = self.bot.get_guild(self.guild_id)
        target_guild = self.bot.get_guild(target_id)
        if source_guild is None:
            return None, "source_guild_missing"
        if target_id == self.guild_id:
            return None, "source_guild_protected"
        if target_guild is None:
            return None, "target_guild_missing"
        return target_guild, None

    def _sync_controls(self) -> None:
        page_guilds = self._page_guilds()
        if page_guilds:
            options = []
            for guild in page_guilds:
                protected = guild.id == self.guild_id
                prefix = "🔒 " if protected else ""
                description = (
                    f"ID {guild.id} · {self._member_count(guild):,} member · "
                    f"{self._channel_count(guild):,} channel"
                )
                if protected:
                    description += " · Được bảo vệ"
                options.append(
                    discord.SelectOption(
                        label=_component_text(
                            f"{prefix}{getattr(guild, 'name', 'Không tên')}",
                            100,
                        ),
                        value=str(guild.id),
                        description=_component_text(description, 100),
                        default=guild.id == self.selected_guild_id,
                    )
                )
            self.guild_select.options = options
            self.guild_select.disabled = self.completed
        else:
            self.guild_select.options = [
                discord.SelectOption(label="Không có server", value="none")
            ]
            self.guild_select.disabled = True

        page_count = max(1, math.ceil(len(self.guilds) / JOINED_SERVER_PAGE_SIZE))
        self.previous_page.disabled = self.completed or self.page == 0
        self.next_page.disabled = self.completed or self.page + 1 >= page_count
        self.refresh_servers.disabled = self.completed
        can_leave = (
            not self.completed
            and self.selected_guild_id is not None
            and self.selected_guild_id != self.guild_id
        )
        self.leave_server.disabled = not can_leave
        self.close.disabled = self.completed

    def build_embed(self) -> discord.Embed:
        page_count = max(1, math.ceil(len(self.guilds) / JOINED_SERVER_PAGE_SIZE))
        embed = discord.Embed(
            title="🌐 Server đã tham gia",
            description=(
                f"Bot đang ở **{len(self.guilds):,}** server. "
                "Server mở bảng được đánh dấu 🔒 và không thể rời từ đây."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        page_guilds = self._page_guilds()
        if not page_guilds:
            embed.add_field(
                name="Không có dữ liệu",
                value="Không tìm thấy server nào trong cache của bot.",
                inline=False,
            )
        for guild in page_guilds:
            protected = guild.id == self.guild_id
            marker = "🔒 " if protected else ""
            status = "\n**Được bảo vệ:** server đang dùng lệnh" if protected else ""
            embed.add_field(
                name=f"{marker}{_safe_display(getattr(guild, 'name', 'Không tên'), 220)}",
                value=(
                    f"ID: `{guild.id}`\n"
                    f"Member: **{self._member_count(guild):,}** · "
                    f"Channel: **{self._channel_count(guild):,}**"
                    f"{status}"
                ),
                inline=False,
            )
        if self.leave_armed and self.selected_guild_id is not None:
            target = next(
                (
                    guild
                    for guild in self.guilds
                    if guild.id == self.selected_guild_id
                ),
                None,
            )
            target_name = getattr(target, "name", "Server không xác định")
            embed.add_field(
                name="⚠️ Cần xác nhận lần hai",
                value=(
                    f"Bạn sắp cho bot rời **{_safe_display(target_name, 180)}** "
                    f"(`{self.selected_guild_id}`). Hành động này không thể hoàn tác "
                    "từ bảng này; bot chỉ có thể quay lại bằng một link mời mới."
                )[:1024],
                inline=False,
            )
        selected = (
            f" · Đã chọn `{self.selected_guild_id}`"
            if self.selected_guild_id is not None
            else ""
        )
        embed.set_footer(text=f"Trang {self.page + 1}/{page_count}{selected}")
        return embed

    async def select_guild(
        self,
        interaction: discord.Interaction,
        selected_value: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            if selected_value == "none":
                return
            try:
                selected_id = int(selected_value)
            except ValueError:
                await interaction.followup.send(
                    "Server đã chọn không hợp lệ.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            page_ids = {guild.id for guild in self._page_guilds()}
            if selected_id not in page_ids or self.bot.get_guild(selected_id) is None:
                await interaction.followup.send(
                    "Server đã chọn không còn thuộc trang hiện tại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            self.selected_guild_id = selected_id
            self._reset_confirmation()
            self._sync_controls()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    async def _change_page(
        self,
        interaction: discord.Interaction,
        offset: int,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            page_count = max(
                1,
                math.ceil(len(self.guilds) / JOINED_SERVER_PAGE_SIZE),
            )
            self.page = max(0, min(self.page + offset, page_count - 1))
            self.selected_guild_id = None
            self._reset_confirmation()
            self._sync_controls()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Trước",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._change_page(interaction, -1)

    @discord.ui.button(
        label="Sau",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._change_page(interaction, 1)

    @discord.ui.button(
        label="Làm mới",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def refresh_servers(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._navigation_lock:
            self.selected_guild_id = None
            self._reset_confirmation()
            self.reload_guilds()
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(
        label="Rời server",
        emoji="🚪",
        style=discord.ButtonStyle.danger,
        row=2,
        disabled=True,
    )
    async def leave_server(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked():
            await _send_private(interaction, "Yêu cầu rời server đang được xử lý.")
            return
        async with self._action_lock:
            if self.completed:
                await _send_private(interaction, "Thao tác rời server đã hoàn tất.")
                return
            target_id = self.selected_guild_id
            if target_id is None or target_id == self.guild_id:
                await _send_private(
                    interaction,
                    "Không thể rời server đang dùng lệnh operation_dashboard.",
                )
                return
            if not self.leave_armed:
                self.leave_armed = True
                self.leave_server.label = "Xác nhận rời"
                self.leave_server.style = discord.ButtonStyle.danger
                await interaction.response.defer(ephemeral=True)
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                return

            # Check once before replacing the panel so a stale/unauthorized
            # confirmation does not misleadingly enter a processing state.
            if not await _check_owner_access(
                interaction,
                bot=self.bot,
                source_guild_id=self.guild_id,
                author_id=int(self.author_id),
            ):
                return
            target_guild, failure_reason = self._resolve_leave_target(target_id)
            if failure_reason is not None:
                await interaction.response.defer(ephemeral=True)
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="failed",
                    details={
                        "target_guild_id": target_id,
                        "reason": failure_reason,
                    },
                )
                self.selected_guild_id = None
                self._reset_confirmation()
                self.reload_guilds()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Server đích không còn khả dụng; danh sách đã được làm mới.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            target_name = str(getattr(target_guild, "name", target_id))
            await interaction.response.defer(ephemeral=True)
            _disable_view(self)
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="🚪 Đang rời server",
                    description=(
                        f"Đang yêu cầu rời **{_safe_display(target_name, 200)}** "
                        f"(`{target_id}`)…"
                    ),
                    color=discord.Color.orange(),
                ),
                view=self,
            )

            # Network waits above can race with permission, ownership, guild
            # removal, or another manager panel. Repeat every guard now, then
            # make the Discord call without any intervening await.
            if not await _check_owner_access(
                interaction,
                bot=self.bot,
                source_guild_id=self.guild_id,
                author_id=int(self.author_id),
            ):
                self._reset_confirmation()
                self._sync_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                return
            target_guild, failure_reason = self._resolve_leave_target(target_id)
            if failure_reason is not None:
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="failed",
                    details={
                        "target_guild_id": target_id,
                        "reason": failure_reason,
                    },
                )
                self.selected_guild_id = None
                self._reset_confirmation()
                self.reload_guilds()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Server nguồn hoặc server đích vừa thay đổi; danh sách đã được làm mới.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            if not self.cog._claim_guild_leave(target_id):
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="failed",
                    details={
                        "target_guild_id": target_id,
                        "reason": "target_leave_in_progress_or_completed",
                    },
                )
                self._reset_confirmation()
                self._sync_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Server này đang được xử lý hoặc bot đã rời server đó.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            try:
                await target_guild.leave()
            except discord.HTTPException as error:
                self.cog._finish_guild_leave(target_id, succeeded=False)
                logger.warning(
                    "Failed to leave guild source=%s target=%s status=%s",
                    self.guild_id,
                    target_id,
                    getattr(error, "status", None),
                )
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="failed",
                    details={
                        "target_guild_id": target_id,
                        "target_guild_name": target_name,
                        "reason": "discord_http_error",
                        "http_status": getattr(error, "status", None),
                        "discord_code": getattr(error, "code", None),
                    },
                )
                self._reset_confirmation()
                self._sync_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Discord từ chối yêu cầu rời server. Bạn có thể xác nhận lại để thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return
            except asyncio.CancelledError:
                # The HTTP request may already have reached Discord. Latch the
                # target so another panel cannot issue a duplicate leave.
                self.cog._finish_guild_leave(target_id, succeeded=True)
                self.completed = True
                _disable_view(self)
                self.stop()
                raise
            except Exception as error:
                self.cog._finish_guild_leave(target_id, succeeded=False)
                logger.exception(
                    "Unexpected guild leave failure source=%s target=%s",
                    self.guild_id,
                    target_id,
                )
                await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="failed",
                    details={
                        "target_guild_id": target_id,
                        "target_guild_name": target_name,
                        "reason": "unexpected_error",
                        "error_type": type(error).__name__,
                    },
                )
                self._reset_confirmation()
                self._sync_controls()
                await interaction.edit_original_response(
                    embed=self.build_embed(),
                    view=self,
                )
                await interaction.followup.send(
                    "Không thể rời server do lỗi ngoài dự kiến. "
                    "Bạn có thể xác nhận lại để thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
                return

            # Latch success before MongoDB audit or response rendering can be
            # cancelled or fail, preventing a second panel from leaving twice.
            self.cog._finish_guild_leave(target_id, succeeded=True)
            self.completed = True
            _disable_view(self)
            self.stop()
            try:
                action_recorded = await self.cog.record_admin_action(
                    interaction=interaction,
                    action="leave_guild",
                    status="succeeded",
                    details={
                        "target_guild_id": target_id,
                        "target_guild_name": target_name,
                    },
                )
            except Exception:
                logger.exception(
                    "Unexpected leave audit failure source=%s target=%s",
                    self.guild_id,
                    target_id,
                )
                action_recorded = False
            audit_note = (
                "Đã ghi audit tại server nguồn."
                if action_recorded
                else "Không thể ghi audit tại server nguồn."
            )
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="✅ Đã rời server",
                    description=(
                        f"Bot đã rời **{_safe_display(target_name, 200)}** "
                        f"(`{target_id}`). {audit_note}"
                    ),
                    color=discord.Color.green(),
                ),
                view=self,
            )

    @discord.ui.button(
        label="Đóng",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._action_lock:
            if self.completed:
                return
            _disable_view(self)
            self.stop()
            await interaction.edit_original_response(
                content="Đã đóng danh sách server.",
                embed=None,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )


class DoctorView(GuildAdminView):
    """Private, read-only diagnostics for the administrator who opened it."""

    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        author_id: int,
        channel: discord.abc.GuildChannel | discord.Thread | None,
    ) -> None:
        super().__init__(guild_id=guild_id, author_id=author_id)
        self.cog = cog
        self.channel = channel
        self.findings: list[SetupCheck] = []
        self.page = 0
        self.generated_at = discord.utils.utcnow()
        self._lock = asyncio.Lock()
        self._sync_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await super().interaction_check(interaction):
            return False
        # Interaction members are snapshots; consult the live cache after scans
        # and queued actions so a revoked administrator cannot read fresh results.
        guild = self.cog.bot.get_guild(self.guild_id)
        member = guild.get_member(interaction.user.id) if guild is not None else None
        if member is None or not member.guild_permissions.administrator:
            await _send_private(
                interaction,
                "Không thể xác minh quyền Administrator hiện tại. "
                "Hãy mở lại operation_dashboard khi quyền và cache đã được cập nhật.",
            )
            return False
        return True

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.findings) / DOCTOR_PAGE_SIZE))

    def _sync_controls(self) -> None:
        self.page = min(max(0, self.page), self.page_count - 1)
        if self.is_finished():
            _disable_view(self)
            return
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.page_count - 1

    async def load_checks(self) -> None:
        guild = self.cog.bot.get_guild(self.guild_id)
        if guild is None:
            checks = [
                SetupCheck(
                    "error",
                    "Server hiện tại",
                    "Không tìm thấy server trong bộ nhớ của bot.",
                    "Mở lại operation_dashboard sau khi bot kết nối lại server.",
                )
            ]
        else:
            checks = await collect_doctor_checks(self.cog.bot, guild, self.channel)
        severity = {"error": 0, "warning": 1}
        self.findings = sorted(
            (check for check in checks if check.level in severity),
            key=lambda check: (severity[check.level], check.name.casefold()),
        )
        self.generated_at = discord.utils.utcnow()
        self.page = 0
        self._sync_controls()

    def build_embed(self) -> discord.Embed:
        self._sync_controls()
        totals = summarize_checks(self.findings)
        color = (
            discord.Color.red()
            if totals["error"]
            else discord.Color.orange()
            if totals["warning"]
            else discord.Color.green()
        )
        embed = discord.Embed(
            title="🩺 Doctor · Chẩn đoán bot",
            description=(
                f"❌ {totals['error']} lỗi · ⚠️ {totals['warning']} cảnh báo\n"
                "Kiểm tra tính năng đang bật trong server hiện tại.\n"
                f"Lần kiểm tra: <t:{int(self.generated_at.timestamp())}:F>"
            ),
            color=color,
            timestamp=self.generated_at,
        )
        if not self.findings:
            embed.add_field(
                name="✅ Không có cảnh báo",
                value="Không phát hiện lỗi hoặc cảnh báo trong các kiểm tra hiện có.",
                inline=False,
            )
        start = self.page * DOCTOR_PAGE_SIZE
        for check in self.findings[start : start + DOCTOR_PAGE_SIZE]:
            icon = "❌" if check.level == "error" else "⚠️"
            name = _safe_display(discord.utils.escape_mentions(check.name), 180)
            detail = _safe_display(discord.utils.escape_mentions(check.detail), 530)
            fix = _safe_display(
                discord.utils.escape_mentions(check.fix or "Kiểm tra cấu hình tính năng."),
                320,
            )
            embed.add_field(
                name=f"{icon} {name}",
                value=f"{detail}\n**Cách sửa:** {fix}",
                inline=False,
            )
        embed.set_footer(
            text=(
                f"Trang {self.page + 1}/{self.page_count} · "
                "Làm mới để kiểm tra lại · Bảng hoạt động 3 phút"
            )
        )
        return embed

    async def _change_page(self, interaction: discord.Interaction, delta: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            if self.is_finished() or not await self.interaction_check(interaction):
                return
            self.page += delta
            await interaction.edit_original_response(
                embed=self.build_embed(), view=self, allowed_mentions=NO_MENTIONS
            )

    @discord.ui.button(label="Trước", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._change_page(interaction, -1)

    @discord.ui.button(label="Sau", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._change_page(interaction, 1)

    @discord.ui.button(label="Làm mới", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            if self.is_finished() or not await self.interaction_check(interaction):
                return
            await self.load_checks()
            if not await self.interaction_check(interaction):
                return
            await interaction.edit_original_response(
                embed=self.build_embed(), view=self, allowed_mentions=NO_MENTIONS
            )

    async def on_timeout(self) -> None:
        async with self._lock:
            await super().on_timeout()


class OperationDashboardView(GuildAdminView):
    def __init__(
        self,
        *,
        cog: "OperationDashboardCog",
        guild_id: int,
        owner_id: int | None = None,
    ) -> None:
        super().__init__(guild_id=guild_id)
        self.cog = cog
        self.owner_id = owner_id
        self._refresh_lock = asyncio.Lock()
        if owner_id is None:
            self.remove_item(self.joined_servers)
            self.remove_item(self.lifecycle_history)

    async def _owner_control_allowed(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if self.owner_id is None:
            await _send_private(
                interaction,
                "Bảng operation_dashboard này không được mở bởi bot owner.",
            )
            return False
        return await _check_owner_access(
            interaction,
            bot=self.cog.bot,
            source_guild_id=self.guild_id,
            author_id=self.owner_id,
        )

    @discord.ui.button(
        label="Làm mới",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._refresh_lock.locked():
            await _send_private(interaction, "Trạng thái đang được làm mới.")
            return
        async with self._refresh_lock:
            await interaction.response.defer()
            embed = await self.cog.build_dashboard_embed(interaction.guild)
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(
        label="Audit logs",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def audit_logs(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = AuditLogView(
            cog=self.cog,
            guild_id=self.guild_id,
            author_id=interaction.user.id,
        )
        try:
            await view.load_page()
        except PyMongoError:
            logger.exception("Failed to open audit browser guild=%s", self.guild_id)
            await interaction.followup.send(
                "Không thể đọc audit log từ MongoDB.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(
        label="Tải CSV",
        emoji="📥",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def export_logs(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        view = ExportLogView(
            cog=self.cog,
            guild_id=self.guild_id,
            author_id=interaction.user.id,
        )
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        view.message = await interaction.original_response()

    @discord.ui.button(
        label="Dọn log",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def prune_logs(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = PruneLogView(
            cog=self.cog,
            guild_id=self.guild_id,
            author_id=interaction.user.id,
        )
        try:
            await view.refresh_preview()
        except PyMongoError:
            logger.exception("Failed to open prune panel guild=%s", self.guild_id)
            await interaction.followup.send(
                "Không thể đếm audit log từ MongoDB.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(
        label="Doctor",
        emoji="🩺",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def doctor(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = DoctorView(
            cog=self.cog,
            guild_id=self.guild_id,
            author_id=interaction.user.id,
            channel=interaction.channel,
        )
        await view.load_checks()
        if not await view.interaction_check(interaction):
            return
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(
        label="Server đã tham gia",
        emoji="🌐",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def joined_servers(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self._owner_control_allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = JoinedServerView(
            cog=self.cog,
            guild_id=self.guild_id,
            author_id=self.owner_id,
        )
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=NO_MENTIONS,
        )

    @discord.ui.button(
        label="Lịch sử kết nối",
        emoji="🔌",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def lifecycle_history(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self._owner_control_allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = LifecycleHistoryView(
            bot=self.cog.bot,
            guild_id=self.guild_id,
            author_id=self.owner_id,
        )
        await view.load_events()
        view.message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=NO_MENTIONS,
        )


class OperationDashboardCog(commands.Cog):
    """Bot health dashboard and guild-scoped command audit trail."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.logs = self.db[LOG_COLLECTION]
        self.loaded_at = discord.utils.utcnow()
        self._index_task: asyncio.Task[None] | None = None
        self._guild_leave_in_flight: set[int] = set()
        self._departed_guild_ids: set[int] = set()

    async def cog_load(self) -> None:
        self._index_task = asyncio.create_task(self._ensure_indexes_until_ready())

    def cog_unload(self) -> None:
        if self._index_task is not None:
            self._index_task.cancel()

    def _ensure_indexes(self) -> bool:
        try:
            self.logs.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="guild_created_at_id",
            )
        except PyMongoError:
            logger.exception("Failed to create operation audit indexes")
            return False
        return True

    async def _ensure_indexes_until_ready(self) -> None:
        while True:
            if await asyncio.to_thread(self._ensure_indexes):
                return
            await asyncio.sleep(INDEX_RETRY_SECONDS)

    def _claim_guild_leave(self, guild_id: int) -> bool:
        if (
            guild_id in self._guild_leave_in_flight
            or guild_id in self._departed_guild_ids
        ):
            return False
        self._guild_leave_in_flight.add(guild_id)
        return True

    def _finish_guild_leave(self, guild_id: int, *, succeeded: bool) -> None:
        self._guild_leave_in_flight.discard(guild_id)
        if succeeded:
            self._departed_guild_ids.add(guild_id)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self._departed_guild_ids.discard(guild.id)

    @staticmethod
    def _should_log_command(ctx: commands.Context) -> bool:
        return ctx.guild is not None and ctx.command is not None

    @staticmethod
    def _event_id(ctx: commands.Context) -> str:
        return f"command:{ctx.message.id}"

    @staticmethod
    def _invoked_name(ctx: commands.Context) -> str:
        parents = [str(value) for value in getattr(ctx, "invoked_parents", ())]
        invoked_with = str(getattr(ctx, "invoked_with", "") or "")
        parts = [*parents, invoked_with]
        parts = [part for part in parts if part]
        command = getattr(ctx, "command", None)
        qualified_name = str(getattr(command, "qualified_name", "") or "")
        command_depth = len(qualified_name.split())
        if command_depth:
            parts = parts[:command_depth]
        return " ".join(parts).strip()

    @classmethod
    def _argument_text(cls, ctx: commands.Context) -> str:
        message = ctx.message
        content = str(getattr(message, "clean_content", message.content))
        prefix = str(getattr(ctx, "prefix", "") or "")
        body = content[len(prefix) :].lstrip() if content.startswith(prefix) else content
        invoked_name = cls._invoked_name(ctx)
        command_word_count = max(1, len(invoked_name.split()))
        parts = body.split(maxsplit=command_word_count)
        arguments = parts[command_word_count] if len(parts) > command_word_count else ""
        return sanitize_command_arguments(arguments)

    @classmethod
    def _command_identity(cls, ctx: commands.Context) -> dict[str, Any]:
        message = ctx.message
        return {
            "_id": cls._event_id(ctx),
            "event_type": "command",
            "guild_id": int(ctx.guild.id),
            "channel_id": int(ctx.channel.id),
            "message_id": int(message.id),
            "actor_id": int(ctx.author.id),
            "actor_name": str(ctx.author),
            "created_at": message.created_at,
        }

    @classmethod
    def _command_fields(
        cls,
        ctx: commands.Context,
        *,
        status: str,
        completed_at: datetime | None,
        error_type_name: str | None,
    ) -> dict[str, Any]:
        command_name = (
            ctx.command.qualified_name if ctx.command is not None else "unknown"
        )
        return {
            "command_name": command_name,
            "invoked_with": cls._invoked_name(ctx),
            "arguments": cls._argument_text(ctx),
            "status": status,
            "completed_at": completed_at,
            "error_type": error_type_name,
        }

    async def _safe_update_command(
        self,
        ctx: commands.Context,
        *,
        status: str,
        completed_at: datetime | None = None,
        error_type_name: str | None = None,
    ) -> None:
        identity = self._command_identity(ctx)
        fields = self._command_fields(
            ctx,
            status=status,
            completed_at=completed_at,
            error_type_name=error_type_name,
        )
        if status == "running":
            update = {"$setOnInsert": {**identity, **fields}}
        else:
            update = {"$setOnInsert": identity, "$set": fields}
        try:
            await asyncio.to_thread(
                self.logs.update_one,
                {"_id": identity["_id"]},
                update,
                upsert=True,
            )
        except DuplicateKeyError:
            if status == "running":
                return
            try:
                await asyncio.to_thread(
                    self.logs.update_one,
                    {"_id": identity["_id"]},
                    {"$set": fields},
                    upsert=False,
                )
            except PyMongoError:
                logger.exception(
                    "Failed to finalize raced command audit guild=%s message=%s",
                    identity["guild_id"],
                    identity["message_id"],
                )
        except PyMongoError:
            logger.exception(
                "Failed to persist command audit guild=%s message=%s status=%s",
                identity["guild_id"],
                identity["message_id"],
                status,
            )

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context) -> None:
        if not self._should_log_command(ctx):
            return
        await self._safe_update_command(ctx, status="running")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        if not self._should_log_command(ctx):
            return
        await self._safe_update_command(
            ctx,
            status="succeeded",
            completed_at=discord.utils.utcnow(),
        )

    @commands.Cog.listener()
    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if not self._should_log_command(ctx):
            return
        await self._safe_update_command(
            ctx,
            status=classify_command_error(error),
            completed_at=discord.utils.utcnow(),
            error_type_name=command_error_type(error),
        )

    async def fetch_audit_page(
        self,
        *,
        guild_id: int,
        range_key: str,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        cutoff = get_audit_cutoff(range_key, now=discord.utils.utcnow())
        bounded_offset = max(0, min(offset, AUDIT_BROWSER_MAX_ROWS - 1))
        bounded_limit = max(1, min(limit, AUDIT_PAGE_SIZE))

        def query_logs() -> tuple[list[dict[str, Any]], int]:
            query: dict[str, Any] = {"guild_id": guild_id}
            if cutoff is not None:
                query["created_at"] = {"$gte": cutoff}
            total = self.logs.count_documents(query)
            cursor = (
                self.logs.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .skip(bounded_offset)
                .limit(bounded_limit)
            )
            return list(cursor), int(total)

        return await asyncio.to_thread(query_logs)

    async def fetch_export_documents(
        self,
        *,
        guild_id: int,
        range_key: str,
    ) -> list[dict[str, Any]]:
        cutoff = get_audit_cutoff(range_key, now=discord.utils.utcnow())

        def query_logs() -> list[dict[str, Any]]:
            query: dict[str, Any] = {"guild_id": guild_id}
            if cutoff is not None:
                query["created_at"] = {"$gte": cutoff}
            cursor = (
                self.logs.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .limit(MAX_EXPORT_ROWS + 1)
            )
            documents = list(cursor)
            if len(documents) > MAX_EXPORT_ROWS:
                raise ExportRowLimitError(
                    f"Audit export exceeds {MAX_EXPORT_ROWS:,} rows"
                )
            return documents

        return await asyncio.to_thread(query_logs)

    async def count_prunable_logs(
        self,
        *,
        guild_id: int,
        cutoff: datetime | None,
    ) -> int:
        query: dict[str, Any] = {"guild_id": guild_id}
        if cutoff is not None:
            query["created_at"] = {"$lt": cutoff}
        return int(await asyncio.to_thread(self.logs.count_documents, query))

    async def prune_logs(
        self,
        *,
        guild_id: int,
        cutoff: datetime | None,
    ) -> int:
        query: dict[str, Any] = {"guild_id": guild_id}
        if cutoff is not None:
            query["created_at"] = {"$lt": cutoff}
        result = await asyncio.to_thread(self.logs.delete_many, query)
        return int(result.deleted_count)

    async def record_admin_action(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        status: str,
        details: dict[str, Any],
    ) -> bool:
        now = discord.utils.utcnow()
        channel_id = interaction.channel_id
        document = {
            "event_type": "admin_action",
            "action": action,
            "guild_id": int(interaction.guild.id),
            "channel_id": int(channel_id) if channel_id is not None else None,
            "actor_id": int(interaction.user.id),
            "actor_name": str(interaction.user),
            "status": status,
            "details": details,
            "created_at": now,
            "completed_at": now,
        }
        try:
            await asyncio.to_thread(self.logs.insert_one, document)
        except PyMongoError:
            logger.exception(
                "Failed to persist operation action guild=%s action=%s status=%s",
                interaction.guild.id,
                action,
                status,
            )
            return False
        return True

    def _bot_started_at(self) -> datetime:
        recorder = getattr(self.bot, "lifecycle_recorder", None)
        process_started_at = getattr(recorder, "process_started_at", None)
        if isinstance(process_started_at, datetime):
            return _as_utc(process_started_at)
        server_stats = self.bot.get_cog("ServerStatsCog")
        started_at = getattr(server_stats, "start_time", None)
        if isinstance(started_at, datetime):
            return _as_utc(started_at)
        return _as_utc(self.loaded_at)

    async def collect_snapshot(self, guild: discord.Guild) -> DashboardSnapshot:
        now = discord.utils.utcnow()

        def query_mongo() -> tuple[int, int, dict[str, int]]:
            ping_started = time.perf_counter()
            self.db.command("ping")
            mongo_latency_ms = round((time.perf_counter() - ping_started) * 1_000)
            retained_logs = self.logs.count_documents({"guild_id": guild.id})
            recent_cutoff = now - timedelta(days=1)
            status_rows = self.logs.aggregate(
                [
                    {
                        "$match": {
                            "guild_id": guild.id,
                            "event_type": "command",
                            "created_at": {"$gte": recent_cutoff},
                        }
                    },
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                ]
            )
            statuses = {
                str(row["_id"]): int(row["count"])
                for row in status_rows
                if row.get("_id") is not None
            }
            return mongo_latency_ms, int(retained_logs), statuses

        mongo_available = True
        mongo_latency_ms: int | None
        retained_logs: int | None
        recent_statuses: dict[str, int]
        try:
            mongo_latency_ms, retained_logs, recent_statuses = await asyncio.to_thread(
                query_mongo
            )
        except PyMongoError:
            logger.exception("Operation dashboard MongoDB health check failed")
            mongo_available = False
            mongo_latency_ms = None
            retained_logs = None
            recent_statuses = {}

        guilds = list(getattr(self.bot, "guilds", ()))
        cached_members = sum(len(item.members) for item in guilds)
        latency = float(getattr(self.bot, "latency", math.nan))
        latency_ms = round(latency * 1_000) if math.isfinite(latency) else None
        return DashboardSnapshot(
            generated_at=now,
            started_at=self._bot_started_at(),
            ready=bool(self.bot.is_ready()),
            latency_ms=latency_ms,
            environment=str(getattr(self.bot, "environment", "production")),
            connected_guilds=len(guilds),
            cached_members=cached_members,
            guild_members=int(guild.member_count or len(guild.members)),
            guild_channels=len(guild.channels),
            mongo_available=mongo_available,
            mongo_latency_ms=mongo_latency_ms,
            retained_logs=retained_logs,
            recent_statuses=recent_statuses,
        )

    async def build_dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        snapshot = await self.collect_snapshot(guild)
        color = (
            discord.Color.green()
            if snapshot.ready and snapshot.mongo_available
            else discord.Color.orange()
        )
        latency = (
            f"{snapshot.latency_ms:,} ms"
            if snapshot.latency_ms is not None
            else "Không xác định"
        )
        mongo_latency = (
            f"{snapshot.mongo_latency_ms:,} ms"
            if snapshot.mongo_latency_ms is not None
            else "Không khả dụng"
        )
        retained = (
            f"{snapshot.retained_logs:,}"
            if snapshot.retained_logs is not None
            else "Không khả dụng"
        )
        recent = snapshot.recent_statuses
        embed = discord.Embed(
            title="🛠️ TFVN bot status",
            description=(
                "✅ Bot đang sẵn sàng."
                if snapshot.ready
                else "⚠️ Bot chưa ở trạng thái sẵn sàng."
            ),
            color=color,
            timestamp=snapshot.generated_at,
        )
        embed.add_field(
            name="Runtime",
            value=(
                f"Chế độ: `{snapshot.environment}`\n"
                f"Khởi chạy: <t:{int(snapshot.started_at.timestamp())}:F>\n"
                f"Uptime: {_format_uptime(snapshot.started_at, snapshot.generated_at)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Discord",
            value=(
                f"WebSocket: **{latency}**\n"
                f"Server đã kết nối: **{snapshot.connected_guilds:,}**\n"
                f"Member đã cache: **{snapshot.cached_members:,}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Server hiện tại",
            value=(
                f"Member: **{snapshot.guild_members:,}**\n"
                f"Channel: **{snapshot.guild_channels:,}**\n"
                f"ID: `{guild.id}`"
            ),
            inline=True,
        )
        mongo_icon = "✅" if snapshot.mongo_available else "❌"
        embed.add_field(
            name="MongoDB & audit",
            value=(
                f"{mongo_icon} Ping: **{mongo_latency}**\n"
                f"Log đang giữ: **{retained}**\n"
                "24 giờ: "
                f"✅ {recent.get('succeeded', 0):,} · "
                f"⛔ {recent.get('denied', 0):,} · "
                f"⚠️ {recent.get('invalid', 0):,} · "
                f"🕒 {recent.get('cooldown', 0):,} · "
                f"⏳ {recent.get('running', 0):,} · "
                f"❌ {recent.get('failed', 0):,}"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Bảng hoạt động 3 phút · Doctor/Audit/CSV/dọn log được trả riêng tư"
        )
        return embed

    @commands.command(
        name="operation_dashboard",
        help="Mở dashboard trạng thái bot, Doctor và audit command.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)
    async def show_operation_dashboard(self, ctx: commands.Context) -> None:
        try:
            is_owner = await self.bot.is_owner(ctx.author)
        except Exception:
            logger.exception(
                "Failed to detect bot owner while opening dashboard guild=%s actor=%s",
                ctx.guild.id,
                ctx.author.id,
            )
            is_owner = False
        view = OperationDashboardView(
            cog=self,
            guild_id=ctx.guild.id,
            owner_id=ctx.author.id if is_owner else None,
        )
        embed = await self.build_dashboard_embed(ctx.guild)
        view.message = await ctx.reply(
            embed=embed,
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    @show_operation_dashboard.error
    async def operation_dashboard_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Lệnh operation_dashboard chỉ dùng được trong server.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Administrator để mở operation_dashboard.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"Hãy thử mở operation_dashboard lại sau {error.retry_after:.1f} giây."
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OperationDashboardCog(bot))
