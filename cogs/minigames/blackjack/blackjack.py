"""Interactive Blackjack backed by the shared Trap Coin balance."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.minigames._casino_ui import (
    BANNER_IDLE,
    BANNER_LOSS,
    BANNER_PUSH,
    BANNER_WIN,
    attach_table_image,
    render_blackjack_table,
)
from cogs.minigames.blackjack._blackjack_helpers import (
    BlackjackGame,
    BlackjackOutcome,
    payout_return,
    score_hand,
)
from cogs.minigames._card_game_economy import (
    DEFAULT_BET,
    MAX_BET,
    MIN_BET,
    CardGameBank,
    parse_wager_input,
    validate_wager,
)
from cogs.minigames._playing_cards import create_deck, format_hand


logger = logging.getLogger(__name__)

BLACKJACK_TIMEOUT_SECONDS = 120
BLACKJACK_TABLE_FILENAME = "blackjack.png"
NO_MENTIONS = discord.AllowedMentions.none()
BANNER_LABELS = {
    BlackjackOutcome.PLAYER_BLACKJACK: "Blackjack!",
    BlackjackOutcome.PLAYER_WIN: "Bạn thắng nhà cái!",
    BlackjackOutcome.DEALER_WIN: "Nhà cái thắng",
    BlackjackOutcome.PUSH: "Hòa — hoàn cược",
}


class BlackjackBetModal(discord.ui.Modal, title="Đặt mức cược"):
    """Collect the next-hand wager without charging until Chơi lại."""

    def __init__(self, table: "BlackjackView") -> None:
        super().__init__(timeout=BLACKJACK_TIMEOUT_SECONDS)
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
                    "Could not reject Blackjack bet modal session=%s",
                    self.table.session_id,
                    exc_info=True,
                )
            return
        await self.table.apply_bet(interaction, bet)


class BlackjackView(discord.ui.View):
    """Owner-only controls and exactly-once in-process game settlement."""

    def __init__(
        self,
        cog: "BlackjackCog",
        *,
        game: BlackjackGame,
        user_id: int,
        guild_id: int | None,
        display_name: str,
        bet: int,
        balance_after_wager: int,
        session_id: str,
    ) -> None:
        super().__init__(timeout=BLACKJACK_TIMEOUT_SECONDS)
        self.cog = cog
        self.game = game
        self.user_id = int(user_id)
        self.guild_id = guild_id
        self.display_name = display_name
        self.bet = int(bet)
        self.session_id = session_id
        self.message: discord.Message | None = None
        self.balance_after = int(balance_after_wager)
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
                "Chỉ người bắt đầu ván Blackjack mới dùng được các nút này.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not reject non-owner Blackjack interaction session=%s",
                self.session_id,
                exc_info=True,
            )
        return False

    def _refresh_controls(self) -> None:
        playing = not self._closed and not self.game.finished
        retry_settle = (
            not self._closed and self.game.finished and not self._settled
        )
        between_hands = (
            not self._closed and self.game.finished and self._settled
        )
        self.hit_button.disabled = not (playing or retry_settle)
        self.stand_button.disabled = not (playing or retry_settle)
        self.play_again_button.disabled = not between_hands
        self.bet_button.disabled = not between_hands

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    def _close_table(self) -> None:
        self._closed = True
        self._disable_controls()
        self.cog.unregister(self)
        self.stop()

    def _outcome_text(self) -> str:
        labels = {
            BlackjackOutcome.PLAYER_BLACKJACK: (
                "🎉 **Blackjack! Bạn thắng với tỷ lệ 3:2.**"
            ),
            BlackjackOutcome.PLAYER_WIN: "🎉 **Bạn thắng nhà cái!**",
            BlackjackOutcome.DEALER_WIN: "💀 **Nhà cái thắng ván này.**",
            BlackjackOutcome.PUSH: "🤝 **Hòa! Tiền cược được trả lại.**",
        }
        return labels.get(self.game.outcome, "Ván bài đã kết thúc.")

    def build_embed(self) -> discord.Embed:
        finished = bool(self.game.finished or self._closed)
        player_cards = format_hand(self.game.player_hand)
        player_value = score_hand(self.game.player_hand).total

        if finished:
            dealer_cards = format_hand(self.game.dealer_hand)
            dealer_value = score_hand(self.game.dealer_hand).total
            dealer_line = f"{dealer_cards}\nĐiểm: **{dealer_value}**"
        else:
            visible = format_hand(self.game.dealer_hand[:1])
            dealer_line = f"{visible}  🂠\nĐiểm: **?**"

        if self._terminal_note is not None:
            status = self._terminal_note
        elif self.game.finished:
            status = self._outcome_text()
            if self._settled and self.return_amount:
                status += f"\nNhận lại: **{self.return_amount:,} TC**."
            elif not self._settled:
                status += "\n⏳ Đang thanh toán kết quả…"
            if self._settled and not self._closed:
                status += "\nNhấn **Chơi lại** hoặc **Đổi cược**."
        else:
            status = "Chọn **Rút bài** hoặc **Dừng**."

        color = discord.Color.blurple()
        if self._settled and self.game.outcome in {
            BlackjackOutcome.PLAYER_BLACKJACK,
            BlackjackOutcome.PLAYER_WIN,
        }:
            color = discord.Color.green()
        elif self._settled and self.game.outcome == BlackjackOutcome.DEALER_WIN:
            color = discord.Color.red()
        elif self._closed:
            color = discord.Color.dark_grey()

        embed = discord.Embed(
            title="🃏 Blackjack",
            description=(
                f"Người chơi: **{self.display_name}**\n"
                f"Cược: **{self.bet:,} TC**\n\n{status}"
            ),
            color=color,
        )
        embed.add_field(
            name="Bài của bạn",
            value=f"{player_cards}\nĐiểm: **{player_value}**",
            inline=False,
        )
        embed.add_field(name="Bài nhà cái", value=dealer_line, inline=False)
        embed.set_footer(text=f"Số dư: {self.balance_after:,} TC")
        return embed

    def _banner_status(self) -> tuple[str, tuple[int, int, int]]:
        if self._terminal_note is not None:
            note = self._terminal_note.split("\n")[0].replace("**", "")[:48]
            return note, BANNER_IDLE
        if self.game.finished and self.game.outcome is not None:
            fill = BANNER_IDLE
            if self.game.outcome in {
                BlackjackOutcome.PLAYER_BLACKJACK,
                BlackjackOutcome.PLAYER_WIN,
            }:
                fill = BANNER_WIN
            elif self.game.outcome is BlackjackOutcome.DEALER_WIN:
                fill = BANNER_LOSS
            elif self.game.outcome is BlackjackOutcome.PUSH:
                fill = BANNER_PUSH
            return BANNER_LABELS[self.game.outcome], fill
        return "Rút bài hoặc Dừng", BANNER_IDLE

    def _render_table(self) -> bytes:
        finished = bool(self.game.finished)
        player_value = score_hand(self.game.player_hand).total
        dealer_value = score_hand(self.game.dealer_hand).total if finished else None
        status, fill = self._banner_status()
        return render_blackjack_table(
            player_hand=self.game.player_hand,
            dealer_hand=self.game.dealer_hand,
            player_total=player_value,
            dealer_total=dealer_value,
            reveal_dealer=finished,
            bet=self.bet,
            balance=self.balance_after,
            status=status,
            banner_fill=fill,
        )

    async def _table_png(self) -> bytes | None:
        try:
            return await asyncio.to_thread(self._render_table)
        except Exception:
            logger.exception(
                "Could not render Blackjack table session=%s",
                self.session_id,
            )
            return None

    async def _send_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_table_image(
            embed,
            await self._table_png(),
            BLACKJACK_TABLE_FILENAME,
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
            BLACKJACK_TABLE_FILENAME,
            for_edit=True,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    def _settle_finished_game(self) -> None:
        """Settle a resolved hand. Caller must hold ``_action_lock``."""

        if self._settled:
            return
        outcome = self.game.outcome
        if not self.game.finished or outcome is None:
            raise RuntimeError("Cannot settle an unfinished Blackjack hand")

        amount = int(payout_return(self.bet, outcome))
        if amount > 0:
            reason = "push" if outcome == BlackjackOutcome.PUSH else "win"
            self.balance_after = self.cog.bank.credit(
                self.user_id,
                self.guild_id,
                "blackjack",
                amount,
                self.session_id,
                reason,
            )
        self.return_amount = amount
        self._settled = True

    async def _safe_interaction_edit(
        self, interaction: discord.Interaction
    ) -> None:
        try:
            await interaction.response.edit_message(**await self._edit_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not update Blackjack interaction session=%s",
                self.session_id,
            )

    async def _safe_message_edit(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**await self._edit_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not edit Blackjack message session=%s",
                self.session_id,
            )

    async def finish_initial_hand(self) -> None:
        """Pay an opening natural only after its Discord message exists."""

        if not self.game.finished:
            return
        async with self._action_lock:
            try:
                self._settle_finished_game()
            except PyMongoError:
                logger.exception(
                    "Could not settle opening Blackjack hand session=%s",
                    self.session_id,
                )
                self._terminal_note = (
                    "⚠️ Chưa thể thanh toán. Hãy bấm một nút để thử lại."
                )
                await self._safe_message_edit()
                return

            self._terminal_note = None
            self._refresh_controls()
            await self._safe_message_edit()

    async def _busy_reply(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.send_message(
                "Ván bài đang xử lý thao tác trước đó, chờ một chút nhé.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not send Blackjack busy reply session=%s",
                self.session_id,
                exc_info=True,
            )

    async def handle_action(
        self, interaction: discord.Interaction, action: str
    ) -> None:
        if self._action_lock.locked():
            await self._busy_reply(interaction)
            return

        async with self._action_lock:
            if self._closed:
                try:
                    await interaction.response.send_message(
                        "Bàn Blackjack này đã đóng.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            if self._settled:
                try:
                    await interaction.response.send_message(
                        "Ván này đã kết thúc. Hãy chọn **Chơi lại** hoặc **Đổi cược**.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return

            # A natural or a previously failed payment is already resolved;
            # pressing either button simply retries its settlement.
            if not self.game.finished:
                if action == "hit":
                    self.game.hit()
                elif action == "stand":
                    self.game.stand()
                else:
                    raise ValueError(f"Unknown Blackjack action: {action}")

            if not self.game.finished:
                self._terminal_note = None
                await self._safe_interaction_edit(interaction)
                return

            try:
                self._settle_finished_game()
            except PyMongoError:
                logger.exception(
                    "Could not settle Blackjack hand session=%s",
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
        """Refund an abandoned hand once, even if lifecycle hooks race."""

        async with self._action_lock:
            if self._closed:
                return
            if not self._settled:
                try:
                    self.balance_after = self.cog.bank.credit(
                        self.user_id,
                        self.guild_id,
                        "blackjack",
                        self.bet,
                        self.session_id,
                        "refund",
                    )
                    self.return_amount = self.bet
                    self._terminal_note = f"{note} Đã hoàn **{self.bet:,} TC**."
                except PyMongoError:
                    logger.exception(
                        "Could not refund Blackjack wager session=%s user=%s",
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
        await self._refund_open_wager(
            "Không thể mở bàn Blackjack.",
            edit=False,
        )

    async def refund_for_unload(self) -> None:
        await self._refund_open_wager(
            "Ván bài dừng vì bot đang tải lại.",
            edit=True,
        )

    async def on_timeout(self) -> None:
        await self._refund_open_wager(
            "⌛ Hết thời gian thao tác.",
            edit=True,
        )

    async def apply_bet(self, interaction: discord.Interaction, bet: int) -> None:
        if self._action_lock.locked():
            await self._busy_reply(interaction)
            return
        async with self._action_lock:
            if self._closed:
                try:
                    await interaction.response.send_message(
                        "Bàn Blackjack này đã đóng.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            if not self._settled:
                try:
                    await interaction.response.send_message(
                        "Không thể đổi cược khi đang chơi ván này.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            self.bet = int(bet)
            self._terminal_note = None
            await self._safe_interaction_edit(interaction)

    async def _begin_new_hand(self, interaction: discord.Interaction) -> None:
        session_id = uuid.uuid4().hex
        try:
            dealt = self.cog.deal_hand(
                user_id=self.user_id,
                guild_id=self.guild_id,
                bet=self.bet,
                session_id=session_id,
            )
        except PyMongoError:
            logger.exception(
                "Could not reserve Blackjack replay session=%s user=%s",
                session_id,
                self.user_id,
            )
            try:
                await interaction.response.send_message(
                    "Không thể trừ tiền cược lúc này. Vui lòng thử lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                pass
            return
        except Exception:
            logger.exception(
                "Could not create Blackjack replay session=%s user=%s",
                session_id,
                self.user_id,
            )
            try:
                await interaction.response.send_message(
                    "Không thể tạo ván Blackjack. Tiền cược đã được hoàn.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                pass
            return

        if dealt is None:
            try:
                await interaction.response.send_message(
                    f"Bạn không có đủ **{self.bet:,} TC** để chơi lại.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                pass
            return

        game, balance_after = dealt
        self.session_id = session_id
        self.game = game
        self.balance_after = balance_after
        self.return_amount = 0
        self._settled = False
        self._terminal_note = None
        if game.finished:
            try:
                self._settle_finished_game()
            except PyMongoError:
                logger.exception(
                    "Could not settle opening Blackjack replay session=%s",
                    session_id,
                )
                self._terminal_note = (
                    "⚠️ Chưa thể thanh toán. Hãy bấm một nút để thử lại."
                )
        self._refresh_controls()
        await self._safe_interaction_edit(interaction)

    @discord.ui.button(
        label="Rút bài",
        emoji="🃏",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def hit_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_action(interaction, "hit")

    @discord.ui.button(
        label="Dừng",
        emoji="✋",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def stand_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.handle_action(interaction, "stand")

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
            await self._busy_reply(interaction)
            return
        async with self._action_lock:
            if self._closed:
                try:
                    await interaction.response.send_message(
                        "Bàn Blackjack này đã đóng.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            if not self._settled:
                try:
                    await interaction.response.send_message(
                        "Hãy kết thúc ván hiện tại trước khi chơi lại.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            await self._begin_new_hand(interaction)

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
            await self._busy_reply(interaction)
            return
        async with self._action_lock:
            if self._closed:
                try:
                    await interaction.response.send_message(
                        "Bàn Blackjack này đã đóng.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            if not self._settled:
                try:
                    await interaction.response.send_message(
                        "Không thể đổi cược khi đang chơi ván này.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return
            try:
                await interaction.response.send_modal(BlackjackBetModal(self))
            except discord.HTTPException:
                logger.exception(
                    "Could not open Blackjack bet modal session=%s",
                    self.session_id,
                )


class BlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_sessions: dict[int, BlackjackView] = {}
        self._starting_users: set[int] = set()
        self._refund_tasks: set[asyncio.Task[Any]] = set()
        self._unloading = False

    def unregister(self, view: BlackjackView) -> None:
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
                    name=f"blackjack-refund-{view.session_id}",
                )
            except RuntimeError:
                logger.exception(
                    "No running loop available to refund Blackjack session=%s",
                    view.session_id,
                )
                continue
            self._refund_tasks.add(task)
            task.add_done_callback(self._refund_tasks.discard)

    async def _send_plain(self, ctx: commands.Context, content: str) -> None:
        await ctx.send(content, allowed_mentions=NO_MENTIONS)

    def deal_hand(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> tuple[BlackjackGame, int] | None:
        """Reserve ``bet`` and deal a new hand. ``None`` means insufficient funds."""

        balance_after = self.bank.reserve_wager(
            user_id,
            guild_id,
            "blackjack",
            bet,
            session_id,
        )
        if balance_after is None:
            return None
        try:
            return BlackjackGame(create_deck()), balance_after
        except Exception:
            logger.exception(
                "Could not create Blackjack hand session=%s user=%s",
                session_id,
                user_id,
            )
            self._refund_setup_failure(
                user_id=user_id,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
            )
            raise

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
                "blackjack",
                bet,
                session_id,
                "refund",
            )
        except PyMongoError:
            logger.exception(
                "Could not refund failed Blackjack setup session=%s user=%s",
                session_id,
                user_id,
            )

    @commands.command(
        name="blackjack",
        help="Chơi Blackjack bằng Trap Coin.",
        usage="blackjack [mức_cược]",
    )
    async def blackjack(
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
                ctx, "Blackjack đang tải lại, bạn thử lại sau một chút nhé."
            )
            return
        if user_id in self._starting_users or user_id in self.active_sessions:
            await self._send_plain(
                ctx,
                "Bạn đang có một bàn Blackjack đang mở. "
                "Hãy chơi tiếp trên bàn đó hoặc đợi bàn đóng.",
            )
            return

        self._starting_users.add(user_id)
        session_id = uuid.uuid4().hex
        try:
            try:
                dealt = self.deal_hand(
                    user_id=user_id,
                    guild_id=guild_id,
                    bet=bet,
                    session_id=session_id,
                )
            except PyMongoError:
                logger.exception(
                    "Could not reserve Blackjack wager session=%s user=%s",
                    session_id,
                    user_id,
                )
                await self._send_plain(
                    ctx, "Không thể trừ tiền cược lúc này. Vui lòng thử lại."
                )
                return
            except Exception:
                await self._send_plain(
                    ctx, "Không thể tạo ván Blackjack. Tiền cược đã được hoàn."
                )
                return

            if dealt is None:
                await self._send_plain(
                    ctx,
                    f"Bạn không có đủ **{bet:,} TC** để chơi Blackjack.",
                )
                return

            game, balance_after = dealt
            try:
                view = BlackjackView(
                    self,
                    game=game,
                    user_id=user_id,
                    guild_id=guild_id,
                    display_name=ctx.author.display_name,
                    bet=bet,
                    balance_after_wager=balance_after,
                    session_id=session_id,
                )
            except Exception:
                logger.exception(
                    "Could not initialize Blackjack table session=%s user=%s",
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
                    ctx, "Không thể tạo ván Blackjack. Tiền cược đã được hoàn."
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
                "Could not send Blackjack table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            return
        except Exception:
            logger.exception(
                "Unexpected failure sending Blackjack table session=%s user=%s",
                session_id,
                user_id,
            )
            await view.refund_send_failure()
            raise

        if game.finished:
            await view.finish_initial_hand()

    @blackjack.error
    async def blackjack_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await self._send_plain(
                ctx,
                "Mức cược phải là số nguyên. Ví dụ: "
                f"`{ctx.clean_prefix}blackjack 50`.",
            )
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlackjackCog(bot))
