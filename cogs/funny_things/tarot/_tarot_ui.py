"""Owner-only tarot picker and card-flip views."""

from __future__ import annotations

import asyncio
import logging

import discord

from cogs.funny_things.tarot._tarot_helpers import (
    SPREAD_ORDER,
    SPREADS,
    Spread,
    TarotReading,
    card_button_label,
    card_field_name,
    card_field_value,
    flip_all_row,
    how_to_flip_tarot,
    how_to_interpret_flip,
    how_to_read_tarot,
    picker_spread_guide,
    reading_status_text,
    spread_position_guide,
)
from cogs.funny_things.tarot._tarot_render import (
    TAROT_FILENAME,
    attach_spread_image,
    render_tarot_spread,
)


logger = logging.getLogger(__name__)

TAROT_TIMEOUT_SECONDS = 300
NO_MENTIONS = discord.AllowedMentions.none()
PICKER_COLOR = 0x6B3FA0
READING_COLOR = 0x6B3FA0
DONE_COLOR = 0xE8B84D
TIMEOUT_COLOR = 0x4A4458
SPREAD_SELECT_CUSTOM_ID = "tarot:spread"
GUIDE_CUSTOM_ID = "tarot:guide"

SPREAD_EMOJIS = {
    "single": "🎴",
    "three": "🔮",
    "five": "✳️",
    "seven": "🌙",
    "celtic": "🌟",
}


async def send_private(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
) -> None:
    payload: dict = {
        "ephemeral": True,
        "allowed_mentions": NO_MENTIONS,
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if "content" not in payload and "embed" not in payload:
        return
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except discord.HTTPException:
        logger.debug("Could not send tarot interaction reply", exc_info=True)


def _interaction_custom_id(interaction: discord.Interaction) -> str:
    data = getattr(interaction, "data", None)
    if not isinstance(data, dict):
        return ""
    return str(data.get("custom_id") or "")


def build_guide_embed(spread: Spread | None = None) -> discord.Embed:
    """Owner-or-anyone ephemeral how-to, with seat meanings when a spread is active."""

    embed = discord.Embed(
        title="🔮 Hướng dẫn Tarot",
        description=how_to_read_tarot(),
        color=PICKER_COLOR,
    )
    embed.add_field(name="Cách lật bài", value=how_to_flip_tarot(), inline=False)
    embed.add_field(
        name="Đọc lá đã lật",
        value=how_to_interpret_flip(),
        inline=False,
    )
    if spread is None:
        embed.add_field(
            name="Các trải bài",
            value=picker_spread_guide(),
            inline=False,
        )
        embed.set_footer(text="Chọn một trải bài trên bảng chính để bắt đầu.")
        return embed
    embed.add_field(
        name=f"Các vị trí — {spread.name_vi}",
        value=spread_position_guide(spread),
        inline=False,
    )
    embed.set_footer(text="Lật bài trên bảng chính; hướng dẫn này chỉ để đọc.")
    return embed


class SpreadSelect(discord.ui.Select):
    def __init__(self, picker: "TarotPickerView") -> None:
        options = [
            discord.SelectOption(
                label=f"{spread.name_vi} ({spread.card_count} lá)",
                value=spread.key,
                description=spread.description[:100],
                emoji=SPREAD_EMOJIS.get(spread.key),
            )
            for spread in (SPREADS[key] for key in SPREAD_ORDER)
        ]
        super().__init__(
            custom_id=SPREAD_SELECT_CUSTOM_ID,
            placeholder="Chọn kiểu trải bài",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.picker = picker

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.picker.interaction_check(interaction):
            return
        key = self.values[0] if self.values else ""
        spread = SPREADS.get(key)
        if spread is None:
            await send_private(interaction, "Kiểu trải bài không hợp lệ. Hãy chọn lại.")
            return
        await self.picker.choose_spread(interaction, spread)


class TarotGuideButton(discord.ui.Button):
    """Ephemeral how-to; anyone may open it, even if they cannot flip cards."""

    def __init__(self, row: int) -> None:
        super().__init__(
            label="Hướng dẫn",
            emoji="📖",
            style=discord.ButtonStyle.secondary,
            custom_id=GUIDE_CUSTOM_ID,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        spread: Spread | None = None
        if isinstance(view, TarotReadingView):
            spread = view.reading.spread
        elif not isinstance(view, TarotPickerView):
            return
        await send_private(interaction, embed=build_guide_embed(spread))


class TarotPickerView(discord.ui.View):
    """Choose a spread before the deck is dealt."""

    def __init__(
        self,
        cog: "TarotCog",
        *,
        owner_id: int,
        display_name: str,
        avatar_url: str | None,
        question: str,
    ) -> None:
        super().__init__(timeout=TAROT_TIMEOUT_SECONDS)
        self.cog = cog
        self.owner_id = int(owner_id)
        self.display_name = display_name
        self.avatar_url = avatar_url
        self.question = question
        self.message: discord.Message | None = None
        self._closed = False
        self._action_lock = asyncio.Lock()
        self.add_item(SpreadSelect(self))
        self.guide_button = TarotGuideButton(row=1)
        self.add_item(self.guide_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _interaction_custom_id(interaction) == GUIDE_CUSTOM_ID:
            return True
        if interaction.user.id == self.owner_id:
            return True
        await send_private(
            interaction,
            "Chỉ người gọi lệnh Tarot mới chọn được kiểu trải bài.",
        )
        return False

    def build_embed(self) -> discord.Embed:
        lines = [
            f"Người hỏi: **{self.display_name}**",
        ]
        if self.question:
            lines.append(f"Câu hỏi: *{self.question}*")
        else:
            lines.append("Không ghi câu hỏi — đọc năng lượng chung.")
        lines.append("")
        lines.append(
            "Chọn kiểu trải bài bên dưới. Bài sẽ được xào và úp xuống. "
            "Nhấn **Hướng dẫn** để xem cách đọc."
        )
        embed = discord.Embed(
            title="🔮 Tarot",
            description="\n".join(lines),
            color=TIMEOUT_COLOR if self._closed else PICKER_COLOR,
        )
        for key in SPREAD_ORDER:
            spread = SPREADS[key]
            embed.add_field(
                name=f"{SPREAD_EMOJIS.get(key, '🎴')} {spread.name_vi}",
                value=f"{spread.card_count} lá — {spread.description}",
                inline=False,
            )
        footer = (
            "Hết hạn. Gọi lại lệnh để bói tiếp."
            if self._closed
            else "Chỉ người hỏi được thao tác • Hết hạn sau 5 phút"
        )
        embed.set_footer(text=footer)
        if self.avatar_url:
            embed.set_author(name=self.display_name, icon_url=self.avatar_url)
        return embed

    def _disable(self) -> None:
        for item in self.children:
            item.disabled = True

    async def choose_spread(
        self,
        interaction: discord.Interaction,
        spread: Spread,
    ) -> None:
        if self._action_lock.locked():
            await send_private(interaction, "Đang xào bài, vui lòng chờ.")
            return
        async with self._action_lock:
            if self._closed:
                await send_private(interaction, "Bảng chọn trải bài đã đóng.")
                return
            self._closed = True
            self._disable()
            self.stop()
            await self.cog.begin_reading(
                interaction,
                spread=spread,
                question=self.question,
                owner_id=self.owner_id,
                display_name=self.display_name,
                avatar_url=self.avatar_url,
            )

    async def on_timeout(self) -> None:
        self._closed = True
        self._disable()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug("Could not expire tarot picker", exc_info=True)


class TarotCardButton(discord.ui.Button["TarotReadingView"]):
    def __init__(self, index: int, *, label: str, row: int) -> None:
        super().__init__(
            label=label,
            emoji="🎴",
            style=discord.ButtonStyle.secondary,
            row=row,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, TarotReadingView):
            await view.flip_one(interaction, self.index)


class TarotFlipAllButton(discord.ui.Button["TarotReadingView"]):
    def __init__(self, row: int) -> None:
        super().__init__(
            label="Lật tất cả",
            emoji="✨",
            style=discord.ButtonStyle.primary,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, TarotReadingView):
            await view.flip_remaining(interaction)


class TarotReadingView(discord.ui.View):
    """Flip each dealt card or reveal the whole spread."""

    def __init__(
        self,
        cog: "TarotCog",
        *,
        reading: TarotReading,
        owner_id: int,
        display_name: str,
        avatar_url: str | None,
    ) -> None:
        super().__init__(timeout=TAROT_TIMEOUT_SECONDS)
        self.cog = cog
        self.reading = reading
        self.owner_id = int(owner_id)
        self.display_name = display_name
        self.avatar_url = avatar_url
        self.message: discord.Message | None = None
        self._closed = False
        self._action_lock = asyncio.Lock()
        self.card_buttons: list[TarotCardButton] = []
        count = reading.spread.card_count
        for index, drawn in enumerate(reading.cards):
            button = TarotCardButton(
                index,
                label=card_button_label(drawn.position, count),
                row=index // 5,
            )
            self.card_buttons.append(button)
            self.add_item(button)
        action_row = flip_all_row(count)
        self.flip_all_button = TarotFlipAllButton(action_row)
        self.add_item(self.flip_all_button)
        self.guide_button = TarotGuideButton(action_row)
        self.add_item(self.guide_button)
        self._refresh_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _interaction_custom_id(interaction) == GUIDE_CUSTOM_ID:
            return True
        if interaction.user.id == self.owner_id:
            return True
        await send_private(
            interaction,
            "Chỉ người hỏi mới lật được các lá bài này.",
        )
        return False

    def _refresh_controls(self) -> None:
        for button in self.card_buttons:
            revealed = self.reading.revealed[button.index]
            button.style = (
                discord.ButtonStyle.success
                if revealed
                else discord.ButtonStyle.secondary
            )
            button.emoji = "✅" if revealed else "🎴"
            button.disabled = self._closed or revealed
        self.flip_all_button.disabled = self._closed or self.reading.all_revealed
        self.guide_button.disabled = self._closed

    def _disable(self) -> None:
        for item in self.children:
            item.disabled = True

    def build_embed(self) -> discord.Embed:
        reading = self.reading
        color = READING_COLOR
        if self._closed and not reading.all_revealed:
            color = TIMEOUT_COLOR
        elif reading.all_revealed:
            color = DONE_COLOR
        lines = [
            f"Người hỏi: **{self.display_name}**",
            f"Trải bài: **{reading.spread.name_vi}** ({reading.spread.card_count} lá)",
        ]
        if reading.question:
            lines.append(f"Câu hỏi: *{reading.question}*")
        else:
            lines.append("Không ghi câu hỏi — đọc năng lượng chung.")
        lines.append("")
        lines.append(reading_status_text(reading))
        if self._closed and not reading.all_revealed:
            lines.append("Hết thời gian lật bài.")
        embed = discord.Embed(
            title=f"🔮 Tarot — {reading.spread.name_vi}",
            description="\n".join(lines),
            color=color,
        )
        for drawn, revealed in zip(reading.cards, reading.revealed, strict=True):
            embed.add_field(
                name=card_field_name(drawn),
                value=card_field_value(drawn, revealed),
                inline=False,
            )
        footer = "Chỉ người hỏi được lật bài • Hết hạn sau 5 phút"
        if self._closed:
            footer = "Phiên bói đã đóng."
        elif reading.all_revealed:
            footer = "Đã lật hết bài."
        embed.set_footer(text=footer)
        if self.avatar_url:
            embed.set_author(name=self.display_name, icon_url=self.avatar_url)
        return embed

    def _render_png(self) -> bytes:
        return render_tarot_spread(self.reading)

    async def _spread_png(self) -> bytes | None:
        try:
            return await asyncio.to_thread(self._render_png)
        except Exception:
            logger.exception("Could not render tarot spread")
            return None

    async def send_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_spread_image(
            embed,
            await self._spread_png(),
            TAROT_FILENAME,
            for_edit=False,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def edit_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_spread_image(
            embed,
            await self._spread_png(),
            TAROT_FILENAME,
            for_edit=True,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def _edit_interaction(self, interaction: discord.Interaction) -> None:
        self._refresh_controls()
        try:
            await interaction.response.edit_message(**await self.edit_kwargs())
        except discord.HTTPException:
            logger.exception("Could not update tarot reading")

    async def flip_one(self, interaction: discord.Interaction, index: int) -> None:
        if self._action_lock.locked():
            await send_private(interaction, "Đang lật bài, vui lòng chờ.")
            return
        async with self._action_lock:
            if self._closed:
                await send_private(interaction, "Phiên bói này đã đóng.")
                return
            if self.reading.revealed[index]:
                await send_private(interaction, "Lá này đã được lật rồi.")
                return
            self.reading.flip(index)
            await self._edit_interaction(interaction)

    async def flip_remaining(self, interaction: discord.Interaction) -> None:
        if self._action_lock.locked():
            await send_private(interaction, "Đang lật bài, vui lòng chờ.")
            return
        async with self._action_lock:
            if self._closed:
                await send_private(interaction, "Phiên bói này đã đóng.")
                return
            flipped = self.reading.flip_all()
            if flipped == 0:
                await send_private(interaction, "Tất cả lá đã được lật.")
                return
            await self._edit_interaction(interaction)

    async def on_timeout(self) -> None:
        self._closed = True
        self._disable()
        if self.message is None:
            return
        try:
            await self.message.edit(**await self.edit_kwargs())
        except discord.HTTPException:
            logger.debug("Could not expire tarot reading", exc_info=True)
