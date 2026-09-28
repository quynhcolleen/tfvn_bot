"""Solo Sic Bo (Tài/Xỉu) backed by the shared Trap Coin balance."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.minigames._card_game_economy import (
    DEFAULT_BET,
    MAX_BET,
    MIN_BET,
    CardGameBank,
    parse_wager_input,
    validate_wager,
)
from cogs.minigames._casino_ui import (
    BANNER_IDLE,
    BANNER_LOSS,
    BANNER_WIN,
    attach_table_image,
    render_sicbo_table,
)
from cogs.minigames.sicbo._sicbo_helpers import (
    BET_LABELS,
    SICBO_GAME_NAME,
    SicBoBet,
    format_dice,
    payout_return,
    roll_dice,
    winning_bet,
)


logger = logging.getLogger(__name__)

SICBO_TIMEOUT_SECONDS = 120
SICBO_TABLE_FILENAME = "sicbo.png"
NO_MENTIONS = discord.AllowedMentions.none()


class SicBoBetModal(discord.ui.Modal, title="Đặt mức cược"):
    """Collect the next-round wager without charging until Chơi lại."""

    def __init__(self, table: "SicBoView") -> None:
        super().__init__(timeout=SICBO_TIMEOUT_SECONDS)
        self.table = table
        self.amount = discord.ui.TextInput(
            label="Mức cược (Trap Coin)",
            placeholder=f"Từ {MIN_BET:,} đến {MAX_BET:,}",
            default=f"{table.bet:,}".replace(",", ""),
            required=True,
            min_length=1,
            max_length=15,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.table.interaction_check(interaction):
            return
        try:
            bet = parse_wager_input(str(self.amount))
        except ValueError as error:
            try:
                await interaction.response.send_message(
                    str(error),
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                logger.debug(
                    "Could not reject Sic Bo bet modal session=%s",
                    self.table.session_id,
                    exc_info=True,
                )
            return
        await self.table.apply_bet(interaction, bet)


class SicBoView(discord.ui.View):
    """Owner-only Tài/Xỉu/Bộ ba controls and exactly-once settlement."""

    def __init__(
        self,
        cog: "SicBoCommandCog",
        *,
        user_id: int,
        guild_id: int | None,
        display_name: str,
        bet: int,
        balance_after_wager: int,
        session_id: str,
    ) -> None:
        super().__init__(timeout=SICBO_TIMEOUT_SECONDS)
        self.cog = cog
        self.user_id = int(user_id)
        self.guild_id = guild_id
        self.display_name = display_name
        self.bet = int(bet)
        self.last_bet = int(bet)
        self.session_id = session_id
        self.balance_after = int(balance_after_wager)
        self.message: discord.Message | None = None
        self.choice: SicBoBet | None = None
        self.dice: tuple[int, int, int] | None = None
        self.return_amount = 0
        self._settled = False
        self._closed = False
        self._terminal_note: str | None = None
        self._action_lock = asyncio.Lock()
        self._refresh_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        try:
            await interaction.response.send_message(
                "Chỉ người bắt đầu ván Sic Bo mới dùng được các nút này.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not reject non-owner Sic Bo interaction session=%s",
                self.session_id,
                exc_info=True,
            )
        return False

    def _refresh_controls(self) -> None:
        playing = not self._closed and not self._settled
        between_rounds = not self._closed and self._settled
        self.big_button.disabled = not playing
        self.small_button.disabled = not playing
        self.triple_button.disabled = not playing
        self.play_again_button.disabled = not between_rounds
        self.bet_button.disabled = not between_rounds

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    def _close_table(self) -> None:
        self._closed = True
        self._disable_controls()
        self.cog.unregister(self)
        self.stop()

    async def _reply_private(
        self,
        interaction: discord.Interaction,
        content: str,
    ) -> None:
        try:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not send Sic Bo private reply session=%s",
                self.session_id,
                exc_info=True,
            )

    def _banner(self) -> tuple[str, tuple[int, int, int]]:
        if self._terminal_note is not None:
            note = self._terminal_note.split("\n")[0].replace("**", "")[:48]
            return note, BANNER_IDLE
        if self.dice is None or self.choice is None:
            return "Chọn Tài, Xỉu hoặc Bộ ba", BANNER_IDLE
        if self.return_amount > 0:
            return f"Thắng {BET_LABELS[self.choice]}!", BANNER_WIN
        return "Trượt mất rồi.", BANNER_LOSS

    def build_embed(self) -> discord.Embed:
        if self._terminal_note is not None:
            status = self._terminal_note
            color = discord.Color.dark_grey()
        elif self.dice is None:
            status = (
                "Chọn **Tài** (11–17), **Xỉu** (4–10) hoặc **Bộ ba**.\n"
                "Tài/Xỉu trả **1:1**. Bộ ba trả **30:1**. "
                "Ba mặt giống nhau làm Tài/Xỉu thua."
            )
            color = discord.Color.gold()
        elif self.return_amount > 0:
            status = (
                f"Xúc xắc: **{format_dice(self.dice)}** "
                f"(tổng **{sum(self.dice)}**)\n"
                f"🎉 **Thắng {BET_LABELS[self.choice]}!** "
                f"Nhận lại **{self.return_amount:,} TC**."
            )
            color = discord.Color.green()
        else:
            winner = winning_bet(self.dice)
            status = (
                f"Xúc xắc: **{format_dice(self.dice)}** "
                f"(tổng **{sum(self.dice)}**)\n"
                f"💀 **{BET_LABELS[winner]} thắng.** Bạn mất "
                f"**{self.last_bet:,} TC**."
            )
            color = discord.Color.red()
        if self._settled and not self._closed:
            status += "\nNhấn **Chơi lại** hoặc **Đổi cược**."

        embed = discord.Embed(
            title="🎲 Sic Bo · Tài Xỉu",
            description=(
                f"Người chơi: **{self.display_name}**\n"
                f"Cược: **{self.bet:,} TC**\n\n{status}"
            ),
            color=color,
        )
        embed.set_footer(text=f"Số dư: {self.balance_after:,} TC")
        return embed

    def _render_table(self) -> bytes:
        status, fill = self._banner()
        return render_sicbo_table(
            dice=self.dice,
            bet=self.bet,
            balance=self.balance_after,
            choice=None if self.choice is None else self.choice.value,
            status=status,
            banner_fill=fill,
        )

    async def _table_png(self) -> bytes | None:
        try:
            return await asyncio.to_thread(self._render_table)
        except Exception:
            logger.exception(
                "Could not render Sic Bo table session=%s",
                self.session_id,
            )
            return None

    async def _send_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_table_image(
            embed,
            await self._table_png(),
            SICBO_TABLE_FILENAME,
            for_edit=False,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def _edit_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_table_image(
            embed,
            await self._table_png(),
            SICBO_TABLE_FILENAME,
            for_edit=True,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def _safe_interaction_edit(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(**await self._edit_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not update Sic Bo interaction session=%s",
                self.session_id,
            )

    async def _safe_message_edit(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**await self._edit_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not edit Sic Bo message session=%s",
                self.session_id,
            )

    def _settle_roll(self, choice: SicBoBet) -> None:
        if self._settled:
            return
        if self.choice is None:
            self.choice = choice
        if self.dice is None:
            self.dice = roll_dice()
        amount = int(payout_return(self.last_bet, self.choice, self.dice))
        if amount > 0:
            self.balance_after = self.cog.bank.credit(
                self.user_id,
                self.guild_id,
                SICBO_GAME_NAME,
                amount,
                self.session_id,
                "win",
            )
        self.return_amount = amount
        self._settled = True

    async def handle_choice(
        self,
        interaction: discord.Interaction,
        choice: SicBoBet,
    ) -> None:
        if self._action_lock.locked():
            await self._reply_private(
                interaction,
                "Ván Sic Bo đang xử lý thao tác trước đó, chờ một chút nhé.",
            )
            return

        async with self._action_lock:
            if self._closed:
                await self._reply_private(
                    interaction, "Bàn Sic Bo này đã đóng."
                )
                return
            if self._settled:
                await self._reply_private(
                    interaction,
                    "Ván này đã kết thúc. Hãy chọn **Chơi lại** hoặc **Đổi cược**.",
                )
                return

            try:
                self._settle_roll(choice)
            except PyMongoError:
                logger.exception(
                    "Could not settle Sic Bo session=%s",
                    self.session_id,
                )
                self._terminal_note = (
                    "⚠️ Chưa thể thanh toán. Hãy bấm một nút để thử lại."
                )
                await self._safe_interaction_edit(interaction)
                return

            self._terminal_note = None
            self._refresh_controls()
            await self._safe_interaction_edit(interaction)

    async def _refund_open_wager(self, note: str, *, edit: bool) -> None:
        async with self._action_lock:
            if self._closed:
                return
            if not self._settled:
                try:
                    self.balance_after = self.cog.bank.credit(
                        self.user_id,
                        self.guild_id,
                        SICBO_GAME_NAME,
                        self.last_bet,
                        self.session_id,
                        "refund",
                    )
                    self.return_amount = self.last_bet
                    self._terminal_note = f"{note} Đã hoàn **{self.last_bet:,} TC**."
                except PyMongoError:
                    logger.exception(
                        "Could not refund Sic Bo wager session=%s user=%s",
                        self.session_id,
                        self.user_id,
                    )
                    self._terminal_note = (
                        f"{note} Không thể hoàn tiền tự động; quản trị viên hãy "
                        f"kiểm tra mã ván `{self.session_id}`."
                    )
                self._settled = True
            self._close_table()
            if edit:
                await self._safe_message_edit()

    async def refund_send_failure(self) -> None:
        await self._refund_open_wager("Không thể mở bàn Sic Bo.", edit=False)

    async def refund_for_unload(self) -> None:
        await self._refund_open_wager(
            "Ván Sic Bo dừng vì bot đang tải lại.",
            edit=True,
        )

    async def on_timeout(self) -> None:
        await self._refund_open_wager("⌛ Hết thời gian thao tác.", edit=True)

    @discord.ui.button(
        label="Tài",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def big_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_choice(interaction, SicBoBet.BIG)

    @discord.ui.button(
        label="Xỉu",
        emoji="🔵",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def small_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_choice(interaction, SicBoBet.SMALL)

    @discord.ui.button(
        label="Bộ ba",
        emoji="⚫",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def triple_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_choice(interaction, SicBoBet.TRIPLE)

    @discord.ui.button(
        label="Chơi lại",
        emoji="🔁",
        style=discord.ButtonStyle.success,
        row=1,
        disabled=True,
    )
    async def play_again_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked():
            await self._reply_private(
                interaction,
                "Ván Sic Bo đang xử lý thao tác trước đó, chờ một chút nhé.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(
                    interaction, "Bàn Sic Bo này đã đóng."
                )
                return
            if not self._settled:
                await self._reply_private(
                    interaction,
                    "Hãy kết thúc ván hiện tại trước khi chơi lại.",
                )
                return
            await self._begin_new_round(interaction)

    @discord.ui.button(
        label="Đổi cược",
        emoji="💰",
        style=discord.ButtonStyle.secondary,
        row=1,
        disabled=True,
    )
    async def bet_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked():
            await self._reply_private(
                interaction,
                "Ván Sic Bo đang xử lý thao tác trước đó, chờ một chút nhé.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(
                    interaction, "Bàn Sic Bo này đã đóng."
                )
                return
            if not self._settled:
                await self._reply_private(
                    interaction,
                    "Không thể đổi cược khi đang chơi ván này.",
                )
                return
            try:
                await interaction.response.send_modal(SicBoBetModal(self))
            except discord.HTTPException:
                logger.exception(
                    "Could not open Sic Bo bet modal session=%s",
                    self.session_id,
                )

    async def apply_bet(self, interaction: discord.Interaction, bet: int) -> None:
        if self._action_lock.locked():
            await self._reply_private(
                interaction,
                "Ván Sic Bo đang xử lý thao tác trước đó, chờ một chút nhé.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(
                    interaction, "Bàn Sic Bo này đã đóng."
                )
                return
            if not self._settled:
                await self._reply_private(
                    interaction,
                    "Không thể đổi cược khi đang chơi ván này.",
                )
                return
            self.bet = int(bet)
            self._terminal_note = None
            await self._safe_interaction_edit(interaction)

    async def _begin_new_round(self, interaction: discord.Interaction) -> None:
        session_id = uuid.uuid4().hex
        try:
            balance_after = self.cog.reserve_round(
                user_id=self.user_id,
                guild_id=self.guild_id,
                bet=self.bet,
                session_id=session_id,
            )
        except PyMongoError:
            logger.exception(
                "Could not reserve Sic Bo replay session=%s user=%s",
                session_id,
                self.user_id,
            )
            await self._reply_private(
                interaction,
                "Không thể trừ tiền cược lúc này. Vui lòng thử lại.",
            )
            return

        if balance_after is None:
            await self._reply_private(
                interaction,
                f"Bạn không có đủ **{self.bet:,} TC** để chơi lại.",
            )
            return

        self.session_id = session_id
        self.last_bet = self.bet
        self.balance_after = balance_after
        self.choice = None
        self.dice = None
        self.return_amount = 0
        self._settled = False
        self._terminal_note = None
        self._refresh_controls()
        await self._safe_interaction_edit(interaction)


class SicBoCommandCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_sessions: dict[int, SicBoView] = {}
        self._starting_users: set[int] = set()
        self._refund_tasks: set[asyncio.Task[Any]] = set()
        self._unloading = False

    def unregister(self, view: SicBoView) -> None:
        if self.active_sessions.get(view.user_id) is view:
            self.active_sessions.pop(view.user_id, None)

    def cog_unload(self) -> None:
        self._unloading = True
        views = list(self.active_sessions.values())
        for view in views:
            self.unregister(view)
            try:
                task = asyncio.create_task(
                    view.refund_for_unload(),
                    name=f"sicbo-refund-{view.session_id}",
                )
            except RuntimeError:
                logger.exception(
                    "No running loop available to refund Sic Bo session=%s",
                    view.session_id,
                )
                continue
            self._refund_tasks.add(task)
            task.add_done_callback(self._refund_tasks.discard)

    async def _send_plain(self, ctx: commands.Context, content: str) -> None:
        await ctx.send(content, allowed_mentions=NO_MENTIONS)

    def reserve_round(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> int | None:
        """Reserve ``bet`` for a new Sic Bo round. ``None`` means insufficient funds."""

        return self.bank.reserve_wager(
            user_id,
            guild_id,
            SICBO_GAME_NAME,
            bet,
            session_id,
        )

    def _refund_setup_failure(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> None:
        try:
            self.bank.credit(
                user_id,
                guild_id,
                SICBO_GAME_NAME,
                bet,
                session_id,
                "refund",
            )
        except PyMongoError:
            logger.exception(
                "Could not refund failed Sic Bo setup session=%s user=%s",
                session_id,
                user_id,
            )

    @commands.command(
        name="sicbo",
        aliases=["sicbo_start"],
        help="Chơi Sic Bo (Tài/Xỉu) bằng Trap Coin.",
        usage="sicbo [mức_cược]",
    )
    async def sicbo(
        self,
        ctx: commands.Context,
        bet: int = DEFAULT_BET,
    ) -> None:
        wager_error = validate_wager(bet)
        if wager_error:
            await self._send_plain(ctx, wager_error)
            return

        user_id = int(ctx.author.id)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        if self._unloading:
            await self._send_plain(
                ctx, "Sic Bo đang tải lại, bạn thử lại sau một chút nhé."
            )
            return
        if user_id in self._starting_users or user_id in self.active_sessions:
            await self._send_plain(
                ctx,
                "Bạn đang có một bàn Sic Bo đang mở. "
                "Hãy chơi tiếp trên bàn đó hoặc đợi bàn đóng.",
            )
            return

        self._starting_users.add(user_id)
        session_id = uuid.uuid4().hex
        try:
            try:
                balance_after = self.reserve_round(
                    user_id=user_id,
                    guild_id=guild_id,
                    bet=bet,
                    session_id=session_id,
                )
            except PyMongoError:
                logger.exception(
                    "Could not reserve Sic Bo wager session=%s user=%s",
                    session_id,
                    user_id,
                )
                await self._send_plain(
                    ctx, "Không thể trừ tiền cược lúc này. Vui lòng thử lại."
                )
                return

            if balance_after is None:
                await self._send_plain(
                    ctx,
                    f"Bạn không có đủ **{bet:,} TC** để chơi Sic Bo.",
                )
                return

            try:
                view = SicBoView(
                    self,
                    user_id=user_id,
                    guild_id=guild_id,
                    display_name=ctx.author.display_name,
                    bet=bet,
                    balance_after_wager=balance_after,
                    session_id=session_id,
                )
            except Exception:
                logger.exception(
                    "Could not initialize Sic Bo table session=%s user=%s",
                    session_id,
                    user_id,
                )
                self._refund_setup_failure(
                    user_id=user_id,
                    guild_id=guild_id,
                    bet=bet,
                    session_id=session_id,
                )
                await self._send_plain(
                    ctx, "Không thể tạo ván Sic Bo. Tiền cược đã được hoàn."
                )
                return
            if self._unloading:
                await view.refund_for_unload()
                return
            self.active_sessions[user_id] = view
        finally:
            self._starting_users.discard(user_id)

        try:
            view.message = await ctx.send(**await view._send_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not send Sic Bo table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            return
        except Exception:
            logger.exception(
                "Unexpected failure sending Sic Bo table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            raise

    @sicbo.error
    async def sicbo_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await self._send_plain(
                ctx,
                "Mức cược phải là số nguyên. Ví dụ: "
                f"`{ctx.clean_prefix}sicbo 50`.",
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SicBoCommandCog(bot))
