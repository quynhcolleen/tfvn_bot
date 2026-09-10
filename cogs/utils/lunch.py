"""Interactive lunch suggestions with offline food art and wish animations."""

import asyncio
from contextlib import closing
from dataclasses import replace
import io
import logging
import math
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from cogs.utils._lunch_helpers import (
    MAX_BUDGET_DIGITS,
    Food,
    LunchFilters,
    choose_food,
    eligible_foods,
    format_price,
    load_foods,
    parse_budget,
    parse_lunch_filters,
    wish_tier,
)
from cogs.utils._lunch_media import LunchMedia


logger = logging.getLogger(__name__)
FOOD_DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "lunch_foods.json"
LUNCH_TIMEOUT_SECONDS = 180
NO_MENTIONS = discord.AllowedMentions.none()
BUDGET_PRESETS = (35, 50, 75, 100, 150, 200)
TIER_COLORS = {"blue": 0x4B9EFF, "purple": 0xA875FF, "gold": 0xE8B84D}
TIER_LABELS = {"blue": "★★★", "purple": "★★★★", "gold": "★★★★★"}


async def send_private(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
        else:
            await interaction.response.send_message(
                content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
    except discord.HTTPException:
        logger.debug("Could not send lunch interaction response", exc_info=True)


def describe_filters(filters: LunchFilters) -> str:
    budget = "Không giới hạn" if filters.budget is None else f"Tối đa {format_price(filters.budget)}"
    diet = "Chỉ món chay" if filters.vegetarian else "Tất cả món"
    return f"💰 {budget} • 🥗 {diet}"


class BudgetModal(discord.ui.Modal):
    def __init__(self, view: "LunchView") -> None:
        super().__init__(title="Ngân sách bữa trưa", timeout=LUNCH_TIMEOUT_SECONDS)
        self.panel = view
        self.budget_input = discord.ui.TextInput(
            label="Ngân sách tối đa (nghìn đồng)",
            placeholder="Ví dụ: 60 hoặc 60k = 60.000₫",
            default=str(view.filters.budget) if view.filters.budget is not None else None,
            max_length=MAX_BUDGET_DIGITS + 1,
        )
        self.add_item(self.budget_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.panel.interaction_check(interaction):
            return
        try:
            budget = parse_budget(str(self.budget_input))
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.panel.set_filters(interaction, replace(self.panel.filters, budget=budget))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.panel.report_error(interaction, error)


class LunchView(discord.ui.View):
    def __init__(
        self,
        cog: "LunchCog",
        author_id: int,
        filters: LunchFilters,
        prefix: str = "!tf ",
    ) -> None:
        super().__init__(timeout=LUNCH_TIMEOUT_SECONDS)
        self.cog = cog
        self.author_id = author_id
        self.filters = filters
        self.prefix = prefix
        self.current_food: Food | None = None
        self.message: discord.Message | None = None
        self.state = "settings"
        self.rolling = False
        self.closed = False
        self._action_lock = asyncio.Lock()
        self._roll_task: asyncio.Task | None = None
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        self.budget_select.options = [
            discord.SelectOption(label="Không giới hạn", value="all", default=self.filters.budget is None),
            *(discord.SelectOption(label=f"Tối đa {format_price(value)}", value=str(value),
                                   default=self.filters.budget == value)
              for value in BUDGET_PRESETS),
            discord.SelectOption(
                label=(f"Tuỳ chọn: {format_price(self.filters.budget)}"
                       if self.filters.budget is not None and self.filters.budget not in BUDGET_PRESETS
                       else "Nhập ngân sách khác…"),
                value="custom",
                default=self.filters.budget is not None and self.filters.budget not in BUDGET_PRESETS,
            ),
        ]
        for option in self.diet_select.options:
            option.default = (option.value == "chay") == self.filters.vegetarian
        settings = self.state == "settings"
        self.budget_select.disabled = not settings
        self.diet_select.disabled = not settings
        candidates = eligible_foods(self.cog.foods, self.filters)
        self.roll_button.label = "Đổi món" if self.state == "result" else "Quay món"
        self.roll_button.disabled = not candidates or (self.state == "result" and len(candidates) < 2)
        self.filters_button.disabled = settings
        if self.rolling:
            self.roll_button.label = "Đang quay…"
        if self.rolling or self.closed:
            for item in self.children:
                item.disabled = True

    def build_settings_embed(self) -> discord.Embed:
        count = len(eligible_foods(self.cog.foods, self.filters))
        embed = discord.Embed(
            title="🍱 Trưa nay ăn gì?",
            description="Chọn ngân sách và chế độ ăn, rồi bấm **✨ Quay món**.",
            color=0xE8B84D,
        )
        embed.add_field(name="Lựa chọn của bạn", value=describe_filters(self.filters), inline=False)
        embed.add_field(
            name="Món phù hợp",
            value=f"**{count} món** • Mỗi món có cơ hội được chọn như nhau."
            if count else "Không có món phù hợp. Hãy tăng ngân sách hoặc đổi chế độ ăn.",
            inline=False,
        )
        embed.set_footer(text="Chỉ người gọi lệnh được thao tác • Hết hạn sau 3 phút không thao tác")
        return embed

    def build_result_embed(self, food: Food) -> discord.Embed:
        tier = wish_tier(food.price)
        embed = discord.Embed(
            title=f"🍽️ {food.name}",
            description=food.sub + (f"\n\n*{food.quip}*" if food.quip else ""),
            color=TIER_COLORS[tier],
        )
        embed.add_field(name="Giá tham khảo / phần", value=format_price(food.price))
        if food.veg:
            embed.add_field(name="Chế độ ăn", value="🌱 Món chay")
        embed.add_field(name="Hạng giá", value=TIER_LABELS[tier])
        embed.add_field(name="Bộ lọc", value=describe_filters(self.filters), inline=False)
        embed.set_image(url=f"attachment://lunch_{food.image}.png")
        embed.set_footer(text="Giá ước tính • Màu quay theo giá món • Hết hạn sau 3 phút không thao tác")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            message = f"Chỉ người gọi lệnh được chọn món. Hãy mở bảng riêng bằng `{self.prefix}lunch`."
        elif self.closed or self.is_finished() or self.cog._unloading:
            message = f"Bảng đã hết hạn. Hãy gọi lại `{self.prefix}lunch`."
        elif self.rolling or self._action_lock.locked():
            message = "Đang xử lý lựa chọn của bạn. Chờ một chút nhé!"
        else:
            return True
        await send_private(interaction, message)
        return False

    async def set_filters(self, interaction: discord.Interaction, filters: LunchFilters) -> None:
        if not await self.interaction_check(interaction):
            return
        if self.state != "settings":
            await send_private(interaction, "Bấm Đổi bộ lọc trước khi thay đổi lựa chọn nhé.")
            return
        async with self._action_lock:
            previous = self.filters
            self.filters = filters
            self._refresh_controls()
            try:
                await interaction.response.edit_message(
                    embed=self.build_settings_embed(), attachments=[], view=self,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                self.filters = previous
                self._refresh_controls()
                raise

    @discord.ui.select(placeholder="💰 Chọn ngân sách tối đa", row=0)
    async def budget_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        if not await self.interaction_check(interaction):
            return
        value = select.values[0]
        if self.state != "settings":
            await send_private(interaction, "Bấm Đổi bộ lọc trước nhé.")
        elif value == "custom":
            await interaction.response.send_modal(BudgetModal(self))
        elif value == "all" or value in {str(preset) for preset in BUDGET_PRESETS}:
            budget = None if value == "all" else int(value)
            await self.set_filters(interaction, replace(self.filters, budget=budget))
        else:
            await send_private(interaction, "Ngân sách không hợp lệ. Hãy chọn lại.")

    @discord.ui.select(
        placeholder="🥗 Chọn chế độ ăn", row=1,
        options=[discord.SelectOption(label="Tất cả món", value="all", emoji="🍽️"),
                 discord.SelectOption(label="Chỉ món chay", value="chay", emoji="🌱")],
    )
    async def diet_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        value = select.values[0]
        if value not in {"all", "chay"}:
            await send_private(interaction, "Chế độ ăn không hợp lệ. Hãy chọn lại.")
            return
        await self.set_filters(interaction, replace(self.filters, vegetarian=value == "chay"))

    @discord.ui.button(label="Quay món", emoji="✨", style=discord.ButtonStyle.primary, row=2)
    async def roll_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.start_roll(interaction)

    @discord.ui.button(label="Đổi bộ lọc", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def filters_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.interaction_check(interaction):
            return
        async with self._action_lock:
            previous = self.state
            self.state = "settings"
            self._refresh_controls()
            try:
                await interaction.response.edit_message(
                    embed=self.build_settings_embed(), attachments=[], view=self,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                self.state = previous
                self._refresh_controls()
                raise

    async def start_roll(self, interaction: discord.Interaction) -> None:
        if not await self.interaction_check(interaction):
            return
        if self.author_id in self.cog.rolling_users:
            await send_private(interaction, "Bạn đang quay món ở một bảng khác. Chờ kết quả trước nhé!")
            return
        candidates = eligible_foods(self.cog.foods, self.filters)
        if not candidates:
            await send_private(interaction, "Không có món phù hợp. Hãy đổi bộ lọc.")
            return
        if self.state == "result" and len(candidates) == 1:
            await send_private(interaction, "Chỉ có một món phù hợp. Hãy đổi bộ lọc để chọn món khác.")
            return

        async with self._action_lock:
            self.cog.rolling_users.add(self.author_id)
            self.rolling = True
            self.state = "rolling"
            self._refresh_controls()
            task = asyncio.current_task()
            self._roll_task = task
            if task is not None:
                self.cog.roll_tasks.add(task)
            try:
                # A GIF upload can take longer than Discord's acknowledgement window.
                await interaction.response.defer()
                food = choose_food(candidates, self.current_food.image if self.current_food else None)
                tier = wish_tier(food.price)
                animation = self.cog.media.wish(tier)
                embed = discord.Embed(
                    title="🌠 Đang cầu nguyện cho bữa trưa…",
                    description="Món ngon đang đến!", color=TIER_COLORS[tier],
                )
                embed.set_image(url="attachment://lunch_wish.gif")
                with closing(discord.File(animation.path, filename="lunch_wish.gif")) as gif:
                    await interaction.edit_original_response(
                        embed=embed, attachments=[gif], view=self, allowed_mentions=NO_MENTIONS,
                    )
                # Render while the animation runs; keep Pillow work off the event loop.
                _, picture = await asyncio.gather(
                    asyncio.sleep(animation.duration_seconds),
                    asyncio.to_thread(self.cog.media.food_png, food.image),
                )
                if self.closed or self.cog._unloading or self.is_finished():
                    return
                self.rolling = False
                self.state = "result"
                self._refresh_controls()
                with closing(discord.File(io.BytesIO(picture), filename=f"lunch_{food.image}.png")) as image:
                    await interaction.edit_original_response(
                        embed=self.build_result_embed(food), attachments=[image], view=self,
                        allowed_mentions=NO_MENTIONS,
                    )
                self.current_food = food
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Discord/network and local image errors share one safe recovery.
                await self.report_error(interaction, error)
                if not self.closed and not self.cog._unloading:
                    self.state = "settings"
                    self.rolling = False
                    self._refresh_controls()
                    try:
                        await interaction.edit_original_response(
                            embed=self.build_settings_embed(), attachments=[], view=self,
                            allowed_mentions=NO_MENTIONS,
                        )
                    except discord.HTTPException:
                        self.close()
            finally:
                self.rolling = False
                self.cog.rolling_users.discard(self.author_id)
                if task is not None:
                    self.cog.roll_tasks.discard(task)
                self._roll_task = None

    async def report_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error("Lunch interaction failed", exc_info=(type(error), error, error.__traceback__))
        await send_private(interaction, "Không thể chọn món lúc này. Hãy thử lại hoặc mở bảng mới.")

    def close(self) -> None:
        self.closed = True
        self._refresh_controls()
        self.stop()
        self.cog.views.discard(self)
        if self._roll_task is not None and self._roll_task is not asyncio.current_task():
            self._roll_task.cancel()

    async def disable_message(self) -> None:
        # An already-started filter edit must finish before the final disabled edit.
        async with self._action_lock:
            if self.message is not None:
                try:
                    await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
                except discord.HTTPException:
                    logger.debug("Could not disable closed lunch panel", exc_info=True)

    async def on_timeout(self) -> None:
        self.close()
        await self.disable_message()

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        await self.report_error(interaction, error)


class LunchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.foods = load_foods(FOOD_DATA_FILE)
        self.media = LunchMedia()
        self.media.validate_foods(tuple(food.image for food in self.foods))
        self.views: set[LunchView] = set()
        self.rolling_users: set[int] = set()
        self.roll_tasks: set[asyncio.Task] = set()
        self._unloading = False

    @commands.command(
        name="lunch", aliases=["antrua", "what_should_i_have_lunch_today"],
        help="Chọn ngân sách, món chay và quay gợi ý ăn trưa kèm hình ảnh.",
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def lunch(self, ctx: commands.Context, *, arguments: str = "") -> None:
        try:
            filters = parse_lunch_filters(arguments)
        except ValueError as error:
            await ctx.send(
                f"{error}\nVí dụ: `{ctx.prefix}lunch`, `{ctx.prefix}lunch 50 chay`.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        view = LunchView(self, ctx.author.id, filters, ctx.prefix)
        self.views.add(view)
        try:
            view.message = await ctx.send(
                embed=view.build_settings_embed(), view=view, allowed_mentions=NO_MENTIONS
            )
        except discord.HTTPException:
            view.close()
            raise

    @lunch.error
    async def lunch_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"Chờ {math.ceil(error.retry_after)} giây rồi mở bảng ăn trưa mới nhé.",
                allowed_mentions=NO_MENTIONS,
            )
        else:
            logger.error("Lunch command failed", exc_info=(type(error), error, error.__traceback__))
            try:
                await ctx.send("Không thể mở bảng ăn trưa lúc này.", allowed_mentions=NO_MENTIONS)
            except discord.HTTPException:
                logger.debug("Could not send lunch command error", exc_info=True)

    async def cog_unload(self) -> None:
        self._unloading = True
        views = tuple(self.views)
        tasks = tuple(self.roll_tasks)
        for view in views:
            view.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(view.disable_message() for view in views))
        self.media.clear_cache()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LunchCog(bot))
