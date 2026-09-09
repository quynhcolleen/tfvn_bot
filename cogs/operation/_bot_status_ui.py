"""Administrator panel for temporary bot activities."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from cogs.operation.bot_status import BotStatusCog

logger = logging.getLogger(__name__)

STATUS_UI_TIMEOUT_SECONDS = 180
NO_MENTIONS = discord.AllowedMentions.none()
ACTIVITY_OPTIONS = (
    ("PLAYING", "Đang chơi, ví dụ Fortnite", "🎮"),
    ("WATCHING", "Đang xem phim hoặc chương trình", "📺"),
    ("LISTENING", "Đang nghe nhạc hoặc nội dung khác", "🎧"),
    ("STREAMING", "Đang phát trực tiếp", "📡"),
    ("COMPETING", "Đang tham gia thi đấu", "🏆"),
    ("CUSTOM", "Dòng trạng thái tùy chỉnh", "💬"),
)


async def send_private(interaction: discord.Interaction, content: str) -> None:
    """Send a mention-free private response before or after acknowledgement."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content=content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
        else:
            await interaction.response.send_message(
                content=content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
    except discord.HTTPException:
        logger.debug("Could not send bot status UI response", exc_info=True)


class BotStatusView(discord.ui.View):
    """Controls owned by the administrator who opened the panel."""

    def __init__(
        self,
        cog: BotStatusCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
    ) -> None:
        super().__init__(timeout=STATUS_UI_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.prefix = prefix
        self.message: discord.Message | None = None

    async def build_embed(self) -> discord.Embed:
        current, mode = await self.cog.describe_status()
        embed = discord.Embed(
            title="🎮 Thiết lập trạng thái bot",
            description=(
                "Chọn loại hoạt động bên dưới, rồi nhập nội dung và thời lượng.\n"
                "Trạng thái này áp dụng cho bot trên **mọi server**."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Trạng thái đã gửi gần nhất", value=current, inline=False)
        embed.add_field(name="Chế độ hiện tại", value=mode, inline=False)
        embed.add_field(
            name="Cách thiết lập",
            value=(
                "Nội dung: **1–128 ký tự**, trên một dòng.\n"
                "Thời lượng: **1 phút–24 giờ**, ví dụ `30m`, `1h`, `1d`.\n"
                "**Đổi ngẫu nhiên** kết thúc trạng thái tạm ngay. "
                "Đóng bảng vẫn giữ trạng thái đến hết thời lượng."
            ),
            inline=False,
        )
        embed.add_field(
            name="Lệnh nhanh",
            value=f"`{self.prefix}bot_status set PLAYING 1h Fortnite`",
            inline=False,
        )
        embed.set_footer(
            text="Chỉ người mở bảng được thao tác • Điều khiển hết hạn sau 3 phút không thao tác"
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            message = "Bảng này chỉ dùng được trong server đã mở nó."
        elif interaction.user.id != self.author_id:
            message = "Chỉ Administrator đã mở bảng được thao tác. Hãy mở bảng riêng bằng bot_status."
        elif not getattr(interaction.user.guild_permissions, "administrator", False):
            message = "Bạn cần quyền Administrator hiện tại để dùng bảng này."
        elif self.is_finished() or self.cog._unloading:
            message = f"Bảng đã đóng hoặc hết hạn. Hãy gọi lại `{self.prefix}bot_status`."
        else:
            return True
        await send_private(interaction, message)
        return False

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def refresh_message(self) -> bool:
        """Refresh the original panel without undoing a successful status change."""
        if self.message is None or self.is_finished() or self.cog._unloading:
            return False
        try:
            await self.message.edit(
                embed=await self.build_embed(), view=self, allowed_mentions=NO_MENTIONS
            )
        except discord.HTTPException:
            logger.debug("Could not refresh bot status panel", exc_info=True)
            return False
        return True

    async def send_action_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await send_private(
                interaction,
                f"Hãy thử đổi trạng thái lại sau {error.retry_after:.1f} giây. "
                "Bảng và lệnh dùng chung thời gian chờ 10 giây trên toàn bot.",
            )
            return
        logger.error(
            "Bot status UI action failed guild=%s user=%s",
            self.guild_id,
            self.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction, "Không thể cập nhật trạng thái bot lúc này. Hãy thử lại sau."
        )

    @discord.ui.select(
        placeholder="Chọn loại trạng thái để thiết lập…",
        options=[
            discord.SelectOption(label=name, value=name, description=description, emoji=emoji)
            for name, description, emoji in ACTIVITY_OPTIONS
        ],
        min_values=1,
        max_values=1,
        row=0,
    )
    async def select_activity(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.send_modal(BotStatusModal(self, select.values[0]))

    @discord.ui.button(label="Đổi ngẫu nhiên", style=discord.ButtonStyle.primary, emoji="🔀", row=1)
    async def reset_status(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            changed = await self.cog.reset_override()
        except Exception as error:
            await self.send_action_error(interaction, error)
            return
        await self.refresh_message()
        await send_private(
            interaction,
            "Đã kết thúc trạng thái tạm và khôi phục đổi ngẫu nhiên trên mọi server."
            if changed else "Bot không có trạng thái tạm; chế độ đổi ngẫu nhiên đang bật.",
        )

    @discord.ui.button(label="Làm mới", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def refresh_status(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        if not await self.refresh_message():
            await send_private(interaction, "Không thể làm mới bảng. Hãy mở lại bot_status.")

    @discord.ui.button(label="Đóng", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def close_panel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        self._disable_controls()
        self.stop()
        await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)

    async def on_timeout(self) -> None:
        self._disable_controls()
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.debug("Could not disable expired bot status panel", exc_info=True)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        await self.send_action_error(interaction, error)


class BotStatusModal(discord.ui.Modal):
    """Text and duration form for one selected activity type."""

    def __init__(self, view: BotStatusView, activity_type: str) -> None:
        super().__init__(title=f"Trạng thái {activity_type}", timeout=STATUS_UI_TIMEOUT_SECONDS)
        self.panel = view
        self.activity_type = activity_type
        current = view.cog.override
        default_text = None
        if current is not None and current.status["type"] == activity_type:
            default_text = current.status.get("text", current.status.get("think"))
        self.text_input = discord.ui.TextInput(
            label="Nội dung trạng thái",
            placeholder="Ví dụ: Fortnite",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=128,
            default=default_text,
        )
        self.duration_input = discord.ui.TextInput(
            label="Thời lượng (1 phút–24 giờ)",
            placeholder="Ví dụ: 30m, 1h hoặc 1d",
            style=discord.TextStyle.short,
            min_length=2,
            max_length=6,
            default="1h",
        )
        self.add_item(self.text_input)
        self.add_item(self.duration_input)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from cogs.operation.bot_status import make_override_status, parse_duration

        if not await self.interaction_check(interaction):
            return
        try:
            status = make_override_status(self.activity_type, self.text_input.value)
            seconds = parse_duration(self.duration_input.value)
        except ValueError as error:
            await send_private(
                interaction, f"{error}\nChọn lại loại trạng thái để mở và sửa biểu mẫu."
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            override = await self.panel.cog.set_override(status, seconds)
        except Exception as error:
            await self.panel.send_action_error(interaction, error)
            return
        await self.panel.refresh_message()
        timestamp = int(override.expires_at.timestamp())
        await send_private(
            interaction,
            f"Đã đặt trạng thái trên mọi server: {self.panel.cog._format_status(status)}\n"
            f"Hết hạn <t:{timestamp}:F> (<t:{timestamp}:R>), sau đó tự đổi ngẫu nhiên.",
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.panel.send_action_error(interaction, error)
