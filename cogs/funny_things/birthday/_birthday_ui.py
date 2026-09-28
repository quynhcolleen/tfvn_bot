import logging
from collections.abc import Awaitable, Callable

import discord


logger = logging.getLogger(__name__)

BIRTHDAY_UI_TIMEOUT_SECONDS = 180

MONTH_SELECT_CUSTOM_ID = "birthday:month"
DAY_SELECT_CUSTOM_ID = "birthday:day"
DAY_PAGE_BUTTON_CUSTOM_ID = "birthday:day-page"
CONFIRM_BUTTON_CUSTOM_ID = "birthday:confirm"
CANCEL_BUTTON_CUSTOM_ID = "birthday:cancel"

_MONTH_LENGTHS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_EMPTY_OPTION_VALUE = "unavailable"
_DAY_PAGE_SIZE = 15

BirthdaySaveCallback = Callable[[int, int], Awaitable[None]]


def days_in_month(month: int) -> int:
    """Return the supported number of days for a birthday month.

    Birthdays have no year, so February intentionally includes February 29.
    """
    if isinstance(month, bool) or not isinstance(month, int):
        raise ValueError("Month must be an integer from 1 to 12")
    if not 1 <= month <= 12:
        raise ValueError("Month must be from 1 to 12")
    return _MONTH_LENGTHS[month - 1]


def is_valid_birthday(month: object, day: object) -> bool:
    """Return whether *month* and *day* form a supported birthday."""
    if (
        isinstance(month, bool)
        or not isinstance(month, int)
        or isinstance(day, bool)
        or not isinstance(day, int)
    ):
        return False
    try:
        return 1 <= day <= days_in_month(month)
    except ValueError:
        return False


def _day_page_for(day: int) -> int:
    return 0 if day <= _DAY_PAGE_SIZE else 1


def _day_page_bounds(month: int, page: int) -> tuple[int, int]:
    last_day = days_in_month(month)
    if page == 0:
        return 1, min(_DAY_PAGE_SIZE, last_day)
    if page == 1 and last_day > _DAY_PAGE_SIZE:
        return _DAY_PAGE_SIZE + 1, last_day
    raise ValueError("Day page is not available for this month")


class MonthSelect(discord.ui.Select):
    def __init__(self, workflow: "BirthdayView") -> None:
        self.workflow = workflow
        super().__init__(
            custom_id=MONTH_SELECT_CUSTOM_ID,
            placeholder="Chọn tháng sinh",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=f"Tháng {month}",
                    value=str(month),
                    default=month == workflow.month,
                )
                for month in range(1, 13)
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.interaction_check(interaction):
            return
        selected = self.values[0] if self.values else None
        valid_values = {str(month): month for month in range(1, 13)}
        month = valid_values.get(selected)
        if month is None:
            await interaction.response.send_message(
                "Tháng đã chọn không hợp lệ. Vui lòng chọn lại.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_month(interaction, month)


class DaySelect(discord.ui.Select):
    def __init__(self, workflow: "BirthdayView") -> None:
        self.workflow = workflow
        super().__init__(
            custom_id=DAY_SELECT_CUSTOM_ID,
            placeholder=self._placeholder(),
            min_values=1,
            max_values=1,
            options=self._build_options(),
            disabled=workflow.month is None,
            row=1,
        )

    def _placeholder(self) -> str:
        if self.workflow.month is None:
            return "Chọn tháng trước"
        start, end = _day_page_bounds(
            self.workflow.month,
            self.workflow.day_page,
        )
        return f"Chọn ngày sinh ({start}–{end})"

    def _build_options(self) -> list[discord.SelectOption]:
        if self.workflow.month is None:
            return [
                discord.SelectOption(
                    label="Chọn tháng trước",
                    value=_EMPTY_OPTION_VALUE,
                )
            ]
        start, end = _day_page_bounds(
            self.workflow.month,
            self.workflow.day_page,
        )
        return [
            discord.SelectOption(
                label=f"Ngày {day}",
                value=str(day),
                default=day == self.workflow.day,
            )
            for day in range(start, end + 1)
        ]

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.interaction_check(interaction):
            return
        if self.workflow.month is None:
            await interaction.response.send_message(
                "Vui lòng chọn tháng trước khi chọn ngày.",
                ephemeral=True,
            )
            return

        start, end = _day_page_bounds(
            self.workflow.month,
            self.workflow.day_page,
        )
        selected = self.values[0] if self.values else None
        valid_days = {str(day): day for day in range(start, end + 1)}
        day = valid_days.get(selected)
        if day is None or not is_valid_birthday(self.workflow.month, day):
            await interaction.response.send_message(
                "Ngày đã chọn không hợp lệ. Vui lòng chọn lại.",
                ephemeral=True,
            )
            return
        await self.workflow.choose_day(interaction, day)


class DayPageButton(discord.ui.Button):
    def __init__(self, workflow: "BirthdayView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Xem ngày 16–31",
            emoji="➡️",
            style=discord.ButtonStyle.secondary,
            custom_id=DAY_PAGE_BUTTON_CUSTOM_ID,
            disabled=workflow.month is None,
            row=2,
        )
        self.sync_display()

    def sync_display(self) -> None:
        self.disabled = self.workflow.month is None
        if self.workflow.month is None:
            self.label = "Xem ngày 16–31"
            self.emoji = "➡️"
            return
        if self.workflow.day_page == 0:
            self.label = f"Xem ngày 16–{days_in_month(self.workflow.month)}"
            self.emoji = "➡️"
        else:
            self.label = "Xem ngày 1–15"
            self.emoji = "⬅️"

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.workflow.interaction_check(interaction):
            return
        await self.workflow.toggle_day_page(interaction)


class ConfirmButton(discord.ui.Button):
    def __init__(self, workflow: "BirthdayView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Xác nhận",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=CONFIRM_BUTTON_CUSTOM_ID,
            disabled=not is_valid_birthday(workflow.month, workflow.day),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.confirm(interaction)


class CancelButton(discord.ui.Button):
    def __init__(self, workflow: "BirthdayView") -> None:
        self.workflow = workflow
        super().__init__(
            label="Hủy",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id=CANCEL_BUTTON_CUSTOM_ID,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.workflow.cancel(interaction)


class BirthdayView(discord.ui.View):
    """Owner-only birthday picker backed by an asynchronous save callback."""

    def __init__(
        self,
        *,
        author_id: int,
        save_callback: BirthdaySaveCallback,
        initial_month: int | None = None,
        initial_day: int | None = None,
    ) -> None:
        super().__init__(timeout=BIRTHDAY_UI_TIMEOUT_SECONDS)
        self.author_id = author_id
        self.save_callback = save_callback
        self.message: discord.Message | None = None
        self.completed = False
        self.submitting = False

        if is_valid_birthday(initial_month, initial_day):
            self.month: int | None = initial_month
            self.day: int | None = initial_day
            self.day_page = _day_page_for(initial_day)
        else:
            self.month = None
            self.day = None
            self.day_page = 0

        self.month_select = MonthSelect(self)
        self.day_select = DaySelect(self)
        self.day_page_button = DayPageButton(self)
        self.confirm_button = ConfirmButton(self)
        self.cancel_button = CancelButton(self)
        self.add_item(self.month_select)
        self.add_item(self.day_select)
        self.add_item(self.day_page_button)
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Chỉ người đã mở bảng ngày sinh này mới có thể sử dụng.",
                ephemeral=True,
            )
            return False
        if self.completed or self.is_finished():
            await interaction.response.send_message(
                "Bảng chọn ngày sinh đã hoàn tất hoặc hết hạn. Hãy gọi lại lệnh.",
                ephemeral=True,
            )
            return False
        if self.submitting:
            await interaction.response.send_message(
                "Ngày sinh đang được lưu, vui lòng chờ.",
                ephemeral=True,
            )
            return False
        return True

    def _sync_components(self) -> None:
        self.month_select.options = [
            discord.SelectOption(
                label=f"Tháng {month}",
                value=str(month),
                default=month == self.month,
            )
            for month in range(1, 13)
        ]
        self.day_select.options = self.day_select._build_options()
        self.day_select.placeholder = self.day_select._placeholder()
        self.day_select.disabled = self.month is None
        self.day_page_button.sync_display()
        self.confirm_button.disabled = not is_valid_birthday(
            self.month,
            self.day,
        )

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎂 Chọn ngày sinh của bạn",
            description=(
                "Chọn **tháng** và **ngày**, sau đó bấm **Xác nhận**. "
                "Dùng nút bên dưới để chuyển giữa ngày 1–15 và 16–cuối tháng."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Tháng",
            value=f"Tháng {self.month}" if self.month is not None else "Chưa chọn",
            inline=True,
        )
        embed.add_field(
            name="Ngày",
            value=str(self.day) if self.day is not None else "Chưa chọn",
            inline=True,
        )
        if is_valid_birthday(self.month, self.day):
            embed.description = (
                f"Bạn đã chọn **{self.day}/{self.month}**. "
                "Bấm **Xác nhận** để lưu ngày sinh."
            )
        embed.set_footer(text="Bảng chọn sẽ hết hạn sau 3 phút.")
        return embed

    async def choose_month(
        self,
        interaction: discord.Interaction,
        month: int,
    ) -> None:
        if not 1 <= month <= 12:
            await interaction.response.send_message(
                "Tháng đã chọn không hợp lệ. Vui lòng chọn lại.",
                ephemeral=True,
            )
            return

        self.month = month
        if not is_valid_birthday(month, self.day):
            self.day = None
            self.day_page = 0
        else:
            self.day_page = _day_page_for(self.day)

        self._sync_components()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def toggle_day_page(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if self.month is None:
            await interaction.response.send_message(
                "Vui lòng chọn tháng trước khi đổi trang ngày.",
                ephemeral=True,
            )
            return
        self.day_page = 1 if self.day_page == 0 else 0
        self.day = None

        self._sync_components()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def choose_day(
        self,
        interaction: discord.Interaction,
        day: int,
    ) -> None:
        if self.month is None:
            await interaction.response.send_message(
                "Vui lòng chọn tháng trước khi chọn ngày.",
                ephemeral=True,
            )
            return
        start, end = _day_page_bounds(self.month, self.day_page)
        if not start <= day <= end or not is_valid_birthday(self.month, day):
            await interaction.response.send_message(
                "Ngày đã chọn không hợp lệ. Vui lòng chọn lại.",
                ephemeral=True,
            )
            return
        self.day = day

        self._sync_components()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def confirm(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if not is_valid_birthday(self.month, self.day):
            await interaction.response.send_message(
                "Vui lòng chọn đầy đủ tháng và ngày sinh hợp lệ.",
                ephemeral=True,
            )
            return

        month = self.month
        day = self.day
        self.submitting = True
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            self.submitting = False
            raise

        try:
            await self.save_callback(month, day)
        except Exception:
            self.submitting = False
            logger.exception(
                "Could not save birthday for user %s",
                self.author_id,
            )
            try:
                await interaction.followup.send(
                    "Không thể lưu ngày sinh lúc này. Vui lòng thử lại.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception(
                    "Could not report birthday save failure for user %s",
                    self.author_id,
                )
            return

        self.submitting = False
        self.completed = True
        self.disable_all()
        success_message = f"Đã đặt sinh nhật của bạn là {day}/{month}. 🎂"
        try:
            await interaction.edit_original_response(
                content=success_message,
                embed=None,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception(
                "Could not update completed birthday UI for user %s",
                self.author_id,
            )
            if self.message is not None:
                try:
                    await self.message.edit(
                        content=success_message,
                        embed=None,
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception(
                        "Could not update stored birthday UI for user %s",
                        self.author_id,
                    )
        finally:
            self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        self.completed = True
        self.disable_all()
        self.stop()
        await interaction.response.edit_message(
            content="Đã hủy đặt ngày sinh.",
            embed=None,
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_timeout(self) -> None:
        self.disable_all()
        self.stop()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            logger.debug(
                "Could not disable expired birthday UI for user %s",
                self.author_id,
                exc_info=True,
            )
