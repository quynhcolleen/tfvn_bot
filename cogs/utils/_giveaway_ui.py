"""Discord panel and modal for creating a giveaway and editing role settings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from cogs.utils._giveaway_helpers import (
    DEFAULT_BONUS_MULTIPLIER,
    MAX_BONUS_MULTIPLIER,
    MAX_DURATION_INPUT_CHARS,
    MAX_DURATION_SECONDS,
    MAX_GIVEAWAY_ROLES,
    MAX_PRIZE_CHARS,
    MAX_WINNERS,
    MAX_WINNERS_INPUT_CHARS,
    MIN_BONUS_MULTIPLIER,
    MIN_DURATION_SECONDS,
    GiveawayCreateError,
    GiveawayFormError,
    GiveawayRoleSettings,
    can_manage_giveaway,
    describe_role_settings,
    format_duration,
    parse_bonus_multiplier,
    parse_giveaway_form,
    parse_role_ids,
)

if TYPE_CHECKING:
    from cogs.utils.giveaway import GiveawayCog

logger = logging.getLogger(__name__)

GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS = 180
NO_MENTIONS = discord.AllowedMentions.none()
MULTIPLIER_PRESETS = (2, 3, 4, 5, 10)


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
        logger.debug("Could not send giveaway create UI response", exc_info=True)


def _role_defaults(role_ids: tuple[int, ...]) -> list[discord.SelectDefaultValue]:
    return [
        discord.SelectDefaultValue(
            id=role_id,
            type=discord.SelectDefaultValueType.role,
        )
        for role_id in role_ids[:MAX_GIVEAWAY_ROLES]
    ]


class GiveawayStaffView(discord.ui.View):
    """Owner-locked giveaway panel with blacklist and bonus-win role controls."""

    persist_role_settings = False

    def __init__(
        self,
        cog: GiveawayCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        settings: GiveawayRoleSettings | None = None,
    ) -> None:
        super().__init__(timeout=GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.prefix = prefix
        self.settings = settings or GiveawayRoleSettings()
        self.message: discord.Message | None = None
        self._creating = False
        self.sync_role_controls()

    def stop(self) -> None:
        super().stop()
        views = getattr(self.cog, "_ui_views", None)
        if views is not None:
            views.discard(self)

    def sync_role_controls(self) -> None:
        for item in list(self.children):
            if isinstance(item, (discord.ui.RoleSelect, discord.ui.Select)):
                self.remove_item(item)
        self.add_item(GiveawayBlacklistRoleSelect(self))
        self.add_item(GiveawayBonusRoleSelect(self))
        self.add_item(GiveawayMultiplierSelect(self))

    def build_embed(self) -> discord.Embed:
        raise NotImplementedError

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            message = "Bảng này chỉ dùng được trong server đã mở nó."
        elif interaction.user.id != self.author_id:
            message = (
                "Chỉ người mở bảng được thao tác. "
                f"Hãy mở bảng riêng bằng `{self.prefix}giveaway`."
            )
        elif not can_manage_giveaway(interaction.user):
            message = (
                "Bạn cần quyền Administrator, Manage Server hoặc "
                "Manage Messages để quản lý giveaway."
            )
        elif self.is_finished() or getattr(self.cog, "_unloading", False):
            message = (
                f"Bảng đã đóng hoặc hết hạn. Hãy gọi lại `{self.prefix}giveaway`."
            )
        elif self._creating:
            message = "Đang tạo giveaway. Chờ một chút nhé!"
        else:
            return True
        await send_private(interaction, message)
        return False

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    async def refresh_message(self) -> None:
        if self.message is None or self.is_finished():
            return
        try:
            await self.message.edit(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug("Could not refresh giveaway panel", exc_info=True)

    async def apply_role_settings(
        self,
        interaction: discord.Interaction,
        settings: GiveawayRoleSettings,
        *,
        announce: str | None = None,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        previous = self.settings
        self.settings = settings
        self.sync_role_controls()
        if self.persist_role_settings:
            try:
                self.cog.save_guild_settings(
                    self.guild_id,
                    settings,
                    updated_by=int(interaction.user.id),
                )
            except Exception:
                self.settings = previous
                self.sync_role_controls()
                logger.exception(
                    "Failed to save giveaway role settings guild=%s",
                    self.guild_id,
                )
                await send_private(
                    interaction,
                    "Không thể lưu cài đặt giveaway lúc này. Hãy thử lại sau.",
                )
                return
        try:
            if interaction.response.is_done():
                await self.refresh_message()
            else:
                await interaction.response.edit_message(
                    embed=self.build_embed(),
                    view=self,
                    allowed_mentions=NO_MENTIONS,
                )
        except discord.HTTPException:
            self.settings = previous
            self.sync_role_controls()
            raise
        overlap = set(settings.blacklist_role_ids).intersection(settings.bonus_role_ids)
        note = announce
        if overlap:
            warning = (
                "Role vừa cấm vừa tăng tỉ lệ sẽ **không được tham gia**, "
                "không được cộng hệ số."
            )
            note = f"{note}\n{warning}" if note else warning
        if note:
            await send_private(interaction, note)

    async def on_timeout(self) -> None:
        self._disable_controls()
        self.stop()
        if self.message is not None:
            try:
                await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.debug("Could not disable expired giveaway panel", exc_info=True)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        logger.error(
            "Giveaway UI action failed guild=%s user=%s",
            self.guild_id,
            self.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể cập nhật bảng giveaway lúc này. Hãy thử lại sau.",
        )


class GiveawayBlacklistRoleSelect(discord.ui.RoleSelect["GiveawayStaffView"]):
    def __init__(self, panel: GiveawayStaffView) -> None:
        super().__init__(
            placeholder="Role không được tham gia…",
            min_values=0,
            max_values=MAX_GIVEAWAY_ROLES,
            default_values=_role_defaults(panel.settings.blacklist_role_ids),
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        settings = GiveawayRoleSettings(
            blacklist_role_ids=parse_role_ids(self.values),
            bonus_role_ids=self.panel.settings.bonus_role_ids,
            bonus_multiplier=self.panel.settings.bonus_multiplier,
        )
        await self.panel.apply_role_settings(interaction, settings)


class GiveawayBonusRoleSelect(discord.ui.RoleSelect["GiveawayStaffView"]):
    def __init__(self, panel: GiveawayStaffView) -> None:
        super().__init__(
            placeholder="Role tăng tỉ lệ thắng…",
            min_values=0,
            max_values=MAX_GIVEAWAY_ROLES,
            default_values=_role_defaults(panel.settings.bonus_role_ids),
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        settings = GiveawayRoleSettings(
            blacklist_role_ids=self.panel.settings.blacklist_role_ids,
            bonus_role_ids=parse_role_ids(self.values),
            bonus_multiplier=self.panel.settings.bonus_multiplier,
        )
        await self.panel.apply_role_settings(interaction, settings)


class GiveawayMultiplierSelect(discord.ui.Select["GiveawayStaffView"]):
    def __init__(self, panel: GiveawayStaffView) -> None:
        current = panel.settings.bonus_multiplier
        options = [
            discord.SelectOption(
                label=f"x{value}",
                value=str(value),
                default=current == value,
                description=f"Role tăng tỉ lệ có {value} lần cơ hội thắng",
            )
            for value in MULTIPLIER_PRESETS
        ]
        custom_selected = current not in MULTIPLIER_PRESETS
        options.append(
            discord.SelectOption(
                label=(
                    f"Tùy chọn: x{current}"
                    if custom_selected
                    else "Tùy chọn…"
                ),
                value="custom",
                default=custom_selected,
                description=f"Nhập hệ số {MIN_BONUS_MULTIPLIER}–{MAX_BONUS_MULTIPLIER}",
            )
        )
        super().__init__(
            placeholder="Hệ số thắng cho role tăng tỉ lệ…",
            min_values=1,
            max_values=1,
            options=options,
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        chosen = self.values[0]
        if chosen == "custom":
            await interaction.response.send_modal(BonusMultiplierModal(self.panel))
            return
        settings = GiveawayRoleSettings(
            blacklist_role_ids=self.panel.settings.blacklist_role_ids,
            bonus_role_ids=self.panel.settings.bonus_role_ids,
            bonus_multiplier=parse_bonus_multiplier(chosen),
        )
        await self.panel.apply_role_settings(
            interaction,
            settings,
            announce=f"Role tăng tỉ lệ thắng có **x{settings.bonus_multiplier}** cơ hội.",
        )


class BonusMultiplierModal(discord.ui.Modal, title="Hệ số thắng"):
    def __init__(self, panel: GiveawayStaffView) -> None:
        super().__init__(timeout=GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS)
        self.panel = panel
        self.multiplier_input = discord.ui.TextInput(
            placeholder=f"{MIN_BONUS_MULTIPLIER}–{MAX_BONUS_MULTIPLIER}",
            style=discord.TextStyle.short,
            min_length=1,
            max_length=2,
            default=str(panel.settings.bonus_multiplier),
            required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="Hệ số thắng",
                description=(
                    f"Role tăng tỉ lệ có n lần cơ hội thắng "
                    f"({MIN_BONUS_MULTIPLIER}–{MAX_BONUS_MULTIPLIER})."
                ),
                component=self.multiplier_input,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        try:
            multiplier = parse_bonus_multiplier(str(self.multiplier_input.value))
        except GiveawayFormError as error:
            await send_private(interaction, str(error))
            return
        settings = GiveawayRoleSettings(
            blacklist_role_ids=self.panel.settings.blacklist_role_ids,
            bonus_role_ids=self.panel.settings.bonus_role_ids,
            bonus_multiplier=multiplier,
        )
        await interaction.response.defer(ephemeral=True)
        await self.panel.apply_role_settings(
            interaction,
            settings,
            announce=f"Role tăng tỉ lệ thắng có **x{multiplier}** cơ hội.",
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            "Giveaway multiplier modal failed guild=%s user=%s",
            self.panel.guild_id,
            self.panel.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể lưu hệ số thắng lúc này. Hãy thử lại sau.",
        )


class GiveawayCreateView(GiveawayStaffView):
    """Owner-locked panel that opens the Discord create form."""

    persist_role_settings = False

    def __init__(
        self,
        cog: GiveawayCog,
        *,
        guild_id: int,
        author_id: int,
        channel_id: int,
        prefix: str,
        settings: GiveawayRoleSettings | None = None,
    ) -> None:
        self.channel_id = channel_id
        super().__init__(
            cog,
            guild_id=guild_id,
            author_id=author_id,
            prefix=prefix,
            settings=settings,
        )

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎉 Tạo giveaway",
            description=(
                "Chọn role cấm / role tăng tỉ lệ, rồi nhấn **Điền biểu mẫu** "
                "để tạo giveaway trong kênh này."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Cài đặt role",
            value=(
                f"{describe_role_settings(self.settings)}\n"
                f"Đổi mặc định server: `{self.prefix}giveaway settings`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Biểu mẫu",
            value=(
                f"Phần thưởng: **1–{MAX_PRIZE_CHARS} ký tự**.\n"
                f"Thời lượng: **{MIN_DURATION_SECONDS} giây–"
                f"{format_duration(MAX_DURATION_SECONDS)}**, "
                "ví dụ `10m`, `1h30m`, `2d`.\n"
                f"Số người thắng: **1–{MAX_WINNERS}**, mặc định `1`."
            ),
            inline=False,
        )
        embed.add_field(
            name="Lệnh nhanh",
            value=f"`{self.prefix}giveaway 10m Nitro Classic`",
            inline=False,
        )
        embed.set_footer(
            text=(
                "Chỉ người mở bảng được thao tác • "
                "Hết hạn sau 3 phút không thao tác"
            )
        )
        return embed

    def build_created_embed(self, message: discord.Message, prize: str) -> discord.Embed:
        embed = discord.Embed(
            title="🎉 Đã tạo giveaway",
            description=(
                f"**Phần thưởng:** {prize}\n"
                f"[Nhảy tới tin nhắn]({message.jump_url})"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Cài đặt role",
            value=describe_role_settings(self.settings),
            inline=False,
        )
        embed.set_footer(text="Biểu mẫu đã đóng")
        return embed

    async def mark_created(self, message: discord.Message, prize: str) -> None:
        self._disable_controls()
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=self.build_created_embed(message, prize),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug("Could not mark giveaway create panel as done", exc_info=True)

    @discord.ui.button(
        label="Điền biểu mẫu",
        style=discord.ButtonStyle.success,
        emoji="📝",
        row=3,
    )
    async def open_form(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        await interaction.response.send_modal(GiveawayCreateModal(self))

    @discord.ui.button(
        label="Đóng",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        row=3,
    )
    async def close_panel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        self._disable_controls()
        self.stop()
        await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)


class GiveawaySettingsView(GiveawayStaffView):
    """Guild defaults for blacklist and bonus-win roles."""

    persist_role_settings = True

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚙️ Cài đặt giveaway",
            description=(
                "Cài đặt này áp dụng cho giveaway mới trên server "
                "(biểu mẫu và lệnh chữ). Giveaway đang chạy giữ bản đã lưu lúc tạo."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Cài đặt hiện tại",
            value=describe_role_settings(self.settings),
            inline=False,
        )
        embed.add_field(
            name="Cấm tham gia",
            value="Người có **một** role trong danh sách không join được.",
            inline=False,
        )
        embed.add_field(
            name="Tăng tỉ lệ thắng",
            value=(
                f"Người có **một** role tăng tỉ lệ có "
                f"**x{self.settings.bonus_multiplier}** vé so với người khác "
                f"(mặc định x{DEFAULT_BONUS_MULTIPLIER}, tối đa x{MAX_BONUS_MULTIPLIER})."
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                "Chỉ người mở bảng được thao tác • "
                "Hết hạn sau 3 phút không thao tác"
            )
        )
        return embed

    @discord.ui.button(
        label="Đóng",
        style=discord.ButtonStyle.secondary,
        emoji="✖️",
        row=3,
    )
    async def close_panel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not await self.interaction_check(interaction):
            return
        self._disable_controls()
        self.stop()
        await interaction.response.edit_message(view=self, allowed_mentions=NO_MENTIONS)


class GiveawayCreateModal(discord.ui.Modal, title="Tạo giveaway"):
    """Initial Discord form: prize, duration, and winner count."""

    def __init__(self, view: GiveawayCreateView) -> None:
        super().__init__(timeout=GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS)
        self.panel = view
        self.prize_input = discord.ui.TextInput(
            placeholder="Ví dụ: Discord Nitro 1 tháng",
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=MAX_PRIZE_CHARS,
            required=True,
        )
        self.duration_input = discord.ui.TextInput(
            placeholder="Ví dụ: 10m, 1h30m hoặc 2d",
            style=discord.TextStyle.short,
            min_length=2,
            max_length=MAX_DURATION_INPUT_CHARS,
            default="1h",
            required=True,
        )
        self.winners_input = discord.ui.TextInput(
            placeholder="1",
            style=discord.TextStyle.short,
            max_length=MAX_WINNERS_INPUT_CHARS,
            default="1",
            required=False,
        )
        self.add_item(
            discord.ui.Label(
                text="Phần thưởng",
                description=f"Tên quà hiện trên tin giveaway, 1–{MAX_PRIZE_CHARS} ký tự.",
                component=self.prize_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Thời lượng",
                description=(
                    f"{MIN_DURATION_SECONDS} giây–{format_duration(MAX_DURATION_SECONDS)}. "
                    "Ví dụ 10m, 1h30m, 2d."
                ),
                component=self.duration_input,
            )
        )
        self.add_item(
            discord.ui.Label(
                text="Số người thắng",
                description=f"Từ 1 đến {MAX_WINNERS}. Để trống thì mặc định 1.",
                component=self.winners_input,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        try:
            draft = parse_giveaway_form(
                prize=str(self.prize_input.value),
                duration=str(self.duration_input.value),
                winners=str(self.winners_input.value),
            )
        except GiveawayFormError as error:
            await send_private(interaction, str(error))
            return

        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            channel = self.panel.cog.bot.get_channel(self.panel.channel_id)
        if channel is None or not hasattr(channel, "send"):
            await send_private(
                interaction,
                "Không tìm thấy kênh để đăng giveaway. Hãy gọi lại lệnh trong kênh text.",
            )
            return

        guild = interaction.guild
        guild_id = guild.id if guild is not None else self.panel.guild_id
        self.panel._creating = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message, _giveaway = await self.panel.cog.start_giveaway(
                guild_id=guild_id,
                channel=channel,
                host_id=int(interaction.user.id),
                prize=draft.prize,
                winner_count=draft.winner_count,
                seconds=draft.seconds,
                settings=self.panel.settings,
            )
        except GiveawayCreateError as error:
            self.panel._creating = False
            await send_private(interaction, str(error))
            return
        except Exception:
            self.panel._creating = False
            logger.exception(
                "Giveaway create form failed guild=%s user=%s",
                self.panel.guild_id,
                self.panel.author_id,
            )
            await send_private(
                interaction,
                "Không thể tạo giveaway lúc này. Hãy thử lại sau.",
            )
            return

        await self.panel.mark_created(message, draft.prize)
        await send_private(
            interaction,
            (
                f"Đã bắt đầu giveaway cho **{draft.prize}** "
                f"({draft.winner_count} người thắng).\n"
                f"Kết thúc sau **{format_duration(draft.seconds)}**.\n"
                f"Tin nhắn: {message.jump_url}"
            ),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        self.panel._creating = False
        logger.error(
            "Giveaway create modal failed guild=%s user=%s",
            self.panel.guild_id,
            self.panel.author_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_private(
            interaction,
            "Không thể tạo giveaway lúc này. Hãy thử lại sau.",
        )
