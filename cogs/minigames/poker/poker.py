"""Solo five-card draw poker played with Trap Coin wagers."""

from __future__ import annotations

import asyncio
import logging
import uuid

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
    BANNER_PUSH,
    BANNER_WIN,
    attach_table_image,
    render_poker_table,
)
from cogs.minigames._playing_cards import create_deck, format_card, format_hand
from cogs.minigames.poker._poker_helpers import (
    PokerGame,
    evaluate_hand,
    rank_label,
)


logger = logging.getLogger(__name__)

POKER_TIMEOUT_SECONDS = 120
POKER_GAME_NAME = "poker"
POKER_TABLE_FILENAME = "poker.png"
HIDDEN_DEALER_HAND = "🂠 🂠 🂠 🂠 🂠"
NO_MENTIONS = discord.AllowedMentions.none()


class PokerBetModal(discord.ui.Modal, title="Đặt mức cược"):
    """Collect the next-hand wager without charging until Chơi lại."""

    def __init__(self, table: "PokerView") -> None:
        super().__init__(timeout=POKER_TIMEOUT_SECONDS)
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
                    "Could not reject poker bet modal session=%s",
                    self.table.session_id,
                    exc_info=True,
                )
            return
        await self.table.apply_bet(interaction, bet)


class PokerCardButton(discord.ui.Button["PokerView"]):
    """Toggle one card in the player's hand for the draw."""

    def __init__(self, card_index: int) -> None:
        super().__init__(
            label=str(card_index + 1),
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.card_index = card_index

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, PokerView):
            await view.toggle_card(interaction, self.card_index)


class PokerView(discord.ui.View):
    """Owner-only controls for one wagered five-card draw round."""

    def __init__(
        self,
        cog: "PokerCog",
        *,
        author_id: int,
        owner_name: str,
        guild_id: int | None,
        bet: int,
        session_id: str,
        game: PokerGame,
        balance_after_reserve: int,
    ) -> None:
        super().__init__(timeout=POKER_TIMEOUT_SECONDS)
        self.cog = cog
        self.author_id = author_id
        self.owner_name = owner_name
        self.guild_id = guild_id
        self.bet = int(bet)
        self.last_bet = int(bet)
        self.session_id = session_id
        self.game = game
        self.balance = balance_after_reserve
        self.message: discord.Message | None = None
        self.selected_indices: set[int] = set()
        self.processing = False
        self.completed = False
        self._money_closed = False
        self._closed = False
        self._action_lock = asyncio.Lock()

        self.card_buttons: list[PokerCardButton] = []
        for card_index in range(5):
            card_button = PokerCardButton(card_index)
            self.card_buttons.append(card_button)
            self.add_item(card_button)
        self._refresh_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Chỉ người mở bàn poker mới dùng được các nút này.",
            ephemeral=True,
            allowed_mentions=NO_MENTIONS,
        )
        return False

    def _disable_controls(self) -> None:
        for child in self.children:
            child.disabled = True

    def _close_table(self) -> None:
        self._closed = True
        self.completed = True
        self.processing = False
        self._disable_controls()
        self.cog.unregister_game(self)
        self.stop()

    def _refresh_controls(self) -> None:
        playing = not self._closed and not self.completed
        between_hands = (
            not self._closed and self.completed and self._money_closed
        )
        for button in self.card_buttons:
            card = self.game.player_hand[button.card_index]
            button.label = format_card(card)
            button.style = (
                discord.ButtonStyle.primary
                if playing and button.card_index in self.selected_indices
                else discord.ButtonStyle.secondary
            )
            button.disabled = not playing
        self.draw_button.disabled = not (playing and self.selected_indices)
        self.stand_button.disabled = not playing
        self.play_again_button.disabled = not between_hands
        self.bet_button.disabled = not between_hands

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
                "Could not send poker private reply session=%s",
                self.session_id,
                exc_info=True,
            )

    def _selection_text(self) -> str:
        if not self.selected_indices:
            return "Chưa chọn lá nào."
        numbers = ", ".join(str(index + 1) for index in sorted(self.selected_indices))
        return f"Đổi lá số **{numbers}**."

    def build_embed(self) -> discord.Embed:
        """Render the table without revealing the dealer before showdown."""
        embed = discord.Embed(
            title="🃏 Poker 5 lá",
            description=(
                f"**{discord.utils.escape_markdown(self.owner_name)}** đang đấu với nhà cái."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Bài của bạn",
            value=format_hand(self.game.player_hand),
            inline=False,
        )
        embed.add_field(
            name="Bài nhà cái",
            value=HIDDEN_DEALER_HAND,
            inline=False,
        )
        embed.add_field(name="Đã chọn", value=self._selection_text(), inline=False)
        embed.add_field(name="Tiền cược", value=f"**{self.bet:,} TC**", inline=True)
        embed.add_field(
            name="Số dư",
            value=f"**{self.balance:,} TC**",
            inline=True,
        )
        embed.set_footer(
            text="Chọn tối đa 3 lá rồi bấm Đổi bài, hoặc bấm Dằn bài."
        )
        return embed

    def _showdown_embed(self) -> discord.Embed:
        player_label = rank_label(evaluate_hand(self.game.player_hand))
        dealer_label = rank_label(evaluate_hand(self.game.dealer_hand))

        stake = self.last_bet
        if self.game.result == 1:
            title = "🎉 Bạn thắng!"
            result_text = (
                f"Bạn nhận **{stake * 2:,} TC** (gồm tiền cược)."
            )
            color = discord.Color.green()
        elif self.game.result == 0:
            title = "🤝 Hòa!"
            result_text = f"Bạn được hoàn **{stake:,} TC**."
            color = discord.Color.blurple()
        else:
            title = "💀 Nhà cái thắng"
            result_text = f"Bạn mất **{stake:,} TC**."
            color = discord.Color.red()
        result_text += f"\nCược ván sau: **{self.bet:,} TC**."
        if not self._closed:
            result_text += "\nNhấn **Chơi lại** hoặc **Đổi cược**."

        embed = discord.Embed(title=title, description=result_text, color=color)
        embed.add_field(
            name=f"Bài của bạn · {player_label}",
            value=format_hand(self.game.player_hand),
            inline=False,
        )
        embed.add_field(
            name=f"Bài nhà cái · {dealer_label}",
            value=format_hand(self.game.dealer_hand),
            inline=False,
        )
        embed.add_field(
            name="Số dư",
            value=f"**{self.balance:,} TC**",
            inline=False,
        )
        return embed

    def _error_embed(self) -> discord.Embed:
        return discord.Embed(
            title="⚠️ Không thể quyết toán bàn poker",
            description=(
                "Đã xảy ra lỗi khi cập nhật Trap Coin. "
                "Vui lòng báo quản trị viên để kiểm tra giao dịch."
            ),
            color=discord.Color.orange(),
        )

    def _banner_status(self, *, showdown: bool) -> tuple[str, tuple[int, int, int]]:
        if not showdown:
            return self._selection_text().replace("**", ""), BANNER_IDLE
        if self.game.result == 1:
            return "Bạn thắng!", BANNER_WIN
        if self.game.result == 0:
            return "Hòa — hoàn cược", BANNER_PUSH
        return "Nhà cái thắng", BANNER_LOSS

    def _render_table(self, *, showdown: bool) -> bytes:
        status, fill = self._banner_status(showdown=showdown)
        player_rank = ""
        dealer_rank = ""
        if showdown:
            player_rank = rank_label(evaluate_hand(self.game.player_hand))
            dealer_rank = rank_label(evaluate_hand(self.game.dealer_hand))
        return render_poker_table(
            player_hand=self.game.player_hand,
            dealer_hand=self.game.dealer_hand,
            reveal_dealer=showdown,
            selected=() if showdown else tuple(sorted(self.selected_indices)),
            bet=self.bet,
            balance=self.balance,
            status=status,
            player_rank=player_rank,
            dealer_rank=dealer_rank,
            banner_fill=fill,
        )

    async def _table_png(self, *, showdown: bool) -> bytes | None:
        try:
            return await asyncio.to_thread(self._render_table, showdown=showdown)
        except Exception:
            logger.exception(
                "Could not render poker table session=%s",
                self.session_id,
            )
            return None

    async def _send_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_table_image(
            embed,
            await self._table_png(showdown=False),
            POKER_TABLE_FILENAME,
            for_edit=False,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def _edit_kwargs(self, embed: discord.Embed, *, showdown: bool) -> dict:
        extra = attach_table_image(
            embed,
            await self._table_png(showdown=showdown),
            POKER_TABLE_FILENAME,
            for_edit=True,
        )
        return {
            "embed": embed,
            "view": self,
            "allowed_mentions": NO_MENTIONS,
            **extra,
        }

    async def toggle_card(
        self,
        interaction: discord.Interaction,
        card_index: int,
    ) -> None:
        if self._closed:
            await self._reply_private(interaction, "Bàn poker này đã đóng.")
            return
        if self.completed or self.processing:
            await self._reply_private(
                interaction,
                "Ván này đã kết thúc. Hãy chọn **Chơi lại** hoặc **Đổi cược**."
                if self.completed
                else "Bàn poker này đang xử lý thao tác trước đó, chờ một chút nhé.",
            )
            return
        if card_index in self.selected_indices:
            self.selected_indices.remove(card_index)
        elif len(self.selected_indices) >= 3:
            await interaction.response.send_message(
                "Bạn chỉ được chọn tối đa **3 lá** để đổi.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return
        else:
            self.selected_indices.add(card_index)

        self._refresh_controls()
        await interaction.response.edit_message(
            **await self._edit_kwargs(self.build_embed(), showdown=False)
        )

    def _settle_wager(self) -> None:
        """Apply the terminal payout at most once."""
        if self._money_closed:
            return
        if self.game.result not in (-1, 0, 1):
            raise RuntimeError("Poker game finished without a valid result")
        self._money_closed = True

        if self.game.result == 1:
            self.balance = self.cog.bank.credit(
                self.author_id,
                self.guild_id,
                POKER_GAME_NAME,
                self.last_bet * 2,
                self.session_id,
                "win",
            )
        elif self.game.result == 0:
            self.balance = self.cog.bank.credit(
                self.author_id,
                self.guild_id,
                POKER_GAME_NAME,
                self.last_bet,
                self.session_id,
                "push",
            )

    def refund_and_close(self, source: str) -> bool:
        """Refund an unfinished round if needed and close the table."""
        if self._closed:
            return False
        refunded = False
        if not self._money_closed:
            self._money_closed = True
            try:
                self.balance = self.cog.bank.credit(
                    self.author_id,
                    self.guild_id,
                    POKER_GAME_NAME,
                    self.last_bet,
                    self.session_id,
                    "refund",
                )
                refunded = True
            except PyMongoError:
                logger.exception(
                    "Failed to refund poker session=%s user=%s source=%s",
                    self.session_id,
                    self.author_id,
                    source,
                )
        self._close_table()
        return refunded

    async def _play(self, interaction: discord.Interaction, *, draw: bool) -> None:
        if self._closed:
            await self._reply_private(interaction, "Bàn poker này đã đóng.")
            return
        if self.completed or self.processing or self._action_lock.locked():
            await self._reply_private(
                interaction,
                "Ván này đã kết thúc. Hãy chọn **Chơi lại** hoặc **Đổi cược**."
                if self.completed
                else "Nước đi này đang được xử lý hoặc bàn đã kết thúc.",
            )
            return
        if draw and not self.selected_indices:
            await self._reply_private(
                interaction,
                "Hãy chọn ít nhất **1 lá** trước khi đổi bài.",
            )
            return

        async with self._action_lock:
            if self._closed or self.completed:
                return
            # Set before any await so rapid clicks cannot settle the wager twice.
            self.processing = True
            try:
                if draw:
                    self.game.draw(sorted(self.selected_indices))
                else:
                    self.game.stand()
                if not self.game.finished:
                    raise RuntimeError("Poker action did not finish the round")
                self._settle_wager()
            except PyMongoError:
                logger.exception(
                    "Failed to settle poker session=%s user=%s",
                    self.session_id,
                    self.author_id,
                )
                self.processing = False
                self._close_table()
                try:
                    await interaction.response.edit_message(
                        **await self._edit_kwargs(
                            self._error_embed(),
                            showdown=False,
                        )
                    )
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.exception(
                        "Could not update failed poker session=%s",
                        self.session_id,
                    )
                return
            except (RuntimeError, ValueError):
                self.processing = False
                logger.exception(
                    "Invalid poker action session=%s user=%s",
                    self.session_id,
                    self.author_id,
                )
                await self._reply_private(
                    interaction,
                    "Không thể thực hiện nước đi này. Vui lòng thử lại.",
                )
                return

            self.completed = True
            self.processing = False
            self.selected_indices.clear()
            self._refresh_controls()
            try:
                await interaction.response.edit_message(
                    **await self._edit_kwargs(
                        self._showdown_embed(),
                        showdown=True,
                    )
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Could not update completed poker session=%s",
                    self.session_id,
                )

    @discord.ui.button(
        label="Đổi bài",
        emoji="🔄",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def draw_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._play(interaction, draw=True)

    @discord.ui.button(
        label="Dằn bài",
        emoji="✋",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def stand_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._play(interaction, draw=False)

    @discord.ui.button(
        label="Chơi lại",
        emoji="🔁",
        style=discord.ButtonStyle.success,
        row=2,
        disabled=True,
    )
    async def play_again_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked() or self.processing:
            await self._reply_private(
                interaction,
                "Nước đi này đang được xử lý hoặc bàn đã kết thúc.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(interaction, "Bàn poker này đã đóng.")
                return
            if not self.completed or not self._money_closed:
                await self._reply_private(
                    interaction,
                    "Hãy kết thúc ván hiện tại trước khi chơi lại.",
                )
                return
            await self._begin_new_hand(interaction)

    @discord.ui.button(
        label="Đổi cược",
        emoji="💰",
        style=discord.ButtonStyle.secondary,
        row=2,
        disabled=True,
    )
    async def bet_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked() or self.processing:
            await self._reply_private(
                interaction,
                "Nước đi này đang được xử lý hoặc bàn đã kết thúc.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(interaction, "Bàn poker này đã đóng.")
                return
            if not self.completed or not self._money_closed:
                await self._reply_private(
                    interaction,
                    "Không thể đổi cược khi đang chơi ván này.",
                )
                return
            try:
                await interaction.response.send_modal(PokerBetModal(self))
            except discord.HTTPException:
                logger.exception(
                    "Could not open poker bet modal session=%s",
                    self.session_id,
                )

    async def apply_bet(self, interaction: discord.Interaction, bet: int) -> None:
        if self._action_lock.locked() or self.processing:
            await self._reply_private(
                interaction,
                "Nước đi này đang được xử lý hoặc bàn đã kết thúc.",
            )
            return
        async with self._action_lock:
            if self._closed:
                await self._reply_private(interaction, "Bàn poker này đã đóng.")
                return
            if not self.completed or not self._money_closed:
                await self._reply_private(
                    interaction,
                    "Không thể đổi cược khi đang chơi ván này.",
                )
                return
            self.bet = int(bet)
            try:
                await interaction.response.edit_message(
                    **await self._edit_kwargs(
                        self._showdown_embed(),
                        showdown=True,
                    )
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Could not update poker bet session=%s",
                    self.session_id,
                )

    async def _begin_new_hand(self, interaction: discord.Interaction) -> None:
        session_id = uuid.uuid4().hex
        try:
            dealt = self.cog.deal_hand(
                user_id=self.author_id,
                guild_id=self.guild_id,
                bet=self.bet,
                session_id=session_id,
            )
        except PyMongoError:
            logger.exception(
                "Could not reserve poker replay session=%s user=%s",
                session_id,
                self.author_id,
            )
            await self._reply_private(
                interaction,
                "Không thể trừ tiền cược lúc này. Vui lòng thử lại.",
            )
            return
        except Exception:
            logger.exception(
                "Could not create poker replay session=%s user=%s",
                session_id,
                self.author_id,
            )
            await self._reply_private(
                interaction,
                "Không thể tạo bàn poker. Tiền cược đã được hoàn.",
            )
            return

        if dealt is None:
            await self._reply_private(
                interaction,
                f"Bạn không có đủ **{self.bet:,} TC** để chơi lại.",
            )
            return

        game, balance = dealt
        self.session_id = session_id
        self.game = game
        self.balance = balance
        self.last_bet = self.bet
        self.selected_indices.clear()
        self.completed = False
        self.processing = False
        self._money_closed = False
        self._refresh_controls()
        try:
            await interaction.response.edit_message(
                **await self._edit_kwargs(self.build_embed(), showdown=False)
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not update poker replay session=%s",
                self.session_id,
            )

    async def on_timeout(self) -> None:
        async with self._action_lock:
            if self._closed:
                return
            had_open_wager = not self._money_closed
            refunded = self.refund_and_close("timeout")

        if self.message is None:
            return
        if not had_open_wager:
            try:
                await self.message.edit(
                    **await self._edit_kwargs(
                        self._showdown_embed(),
                        showdown=True,
                    )
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Could not disable timed-out poker session=%s",
                    self.session_id,
                )
            return
        description = (
            f"Bàn đã hết thời gian. Đã hoàn **{self.last_bet:,} TC**."
            if refunded
            else "Bàn đã hết thời gian nhưng không thể tự động hoàn Trap Coin."
        )
        embed = discord.Embed(
            title="⌛ Bàn poker đã hết hạn",
            description=description,
            color=discord.Color.dark_grey(),
        )
        try:
            await self.message.edit(
                embed=embed,
                view=self,
                attachments=[],
                allowed_mentions=NO_MENTIONS,
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Could not disable timed-out poker session=%s",
                self.session_id,
            )


class PokerCog(commands.Cog):
    """Run solo five-card draw tables backed by the shared Trap Coin bank."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_games: dict[int, PokerView] = {}

    def unregister_game(self, view: PokerView) -> None:
        current = self.active_games.get(view.author_id)
        if current is view:
            self.active_games.pop(view.author_id, None)

    def cog_unload(self) -> None:
        for view in tuple(self.active_games.values()):
            view.refund_and_close("cog_unload")
        self.active_games.clear()

    def deal_hand(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        bet: int,
        session_id: str,
    ) -> tuple[PokerGame, int] | None:
        """Reserve ``bet`` and deal a new hand. ``None`` means insufficient funds."""

        balance = self.bank.reserve_wager(
            user_id,
            guild_id,
            POKER_GAME_NAME,
            bet,
            session_id,
        )
        if balance is None:
            return None
        try:
            return PokerGame(create_deck()), balance
        except Exception:
            logger.exception(
                "Failed to create poker game session=%s user=%s",
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
    ) -> bool:
        try:
            self.bank.credit(
                user_id,
                guild_id,
                POKER_GAME_NAME,
                bet,
                session_id,
                "refund",
            )
        except PyMongoError:
            logger.exception(
                "Failed to refund poker setup session=%s user=%s",
                session_id,
                user_id,
            )
            return False
        return True

    @commands.command(
        name="poker",
        help="Chơi poker 5 lá với nhà cái bằng Trap Coin.",
    )
    async def poker(self, ctx: commands.Context, bet: int = DEFAULT_BET) -> None:
        wager_error = validate_wager(bet)
        if wager_error is not None:
            await ctx.send(wager_error, allowed_mentions=NO_MENTIONS)
            return

        if ctx.author.id in self.active_games:
            await ctx.send(
                "Bạn đang có một bàn poker đang mở. "
                "Hãy chơi tiếp trên bàn đó hoặc đợi bàn đóng.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        session_id = uuid.uuid4().hex
        guild_id = ctx.guild.id if ctx.guild is not None else None
        try:
            dealt = self.deal_hand(
                user_id=ctx.author.id,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
            )
        except PyMongoError:
            logger.exception("Failed to reserve poker wager user=%s", ctx.author.id)
            await ctx.send(
                "Không thể truy cập số dư Trap Coin. Vui lòng thử lại.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        except Exception:
            await ctx.send(
                "Không thể tạo bàn poker. Tiền cược đã được hoàn.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if dealt is None:
            await ctx.send(
                f"Bạn không có đủ Trap Coin để cược **{bet:,} TC**.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        game, balance = dealt

        try:
            view = PokerView(
                self,
                author_id=ctx.author.id,
                owner_name=ctx.author.display_name,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
                game=game,
                balance_after_reserve=balance,
            )
        except Exception:
            logger.exception(
                "Failed to initialize poker table session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            refunded = self._refund_setup_failure(
                user_id=ctx.author.id,
                guild_id=guild_id,
                bet=bet,
                session_id=session_id,
            )
            message = (
                "Không thể tạo bàn poker. Tiền cược đã được hoàn."
                if refunded
                else (
                    "Không thể tạo bàn poker và chưa thể tự động hoàn tiền. "
                    f"Hãy báo quản trị viên mã ván `{session_id}`."
                )
            )
            await ctx.send(message, allowed_mentions=NO_MENTIONS)
            return

        self.active_games[ctx.author.id] = view
        try:
            view.message = await ctx.send(**await view._send_kwargs())
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed to send poker table; refunding session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            view.refund_and_close("send_failure")
        except Exception:
            logger.exception(
                "Unexpected failure sending poker table; refunding session=%s user=%s",
                session_id,
                ctx.author.id,
            )
            view.refund_and_close("send_failure")
            raise

    @poker.error
    async def poker_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.send(
                f"Tiền cược phải là số nguyên từ **{MIN_BET:,}** "
                f"đến **{MAX_BET:,} TC**.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        logger.exception("Unexpected poker command error")
        await ctx.send(
            "Đã xảy ra lỗi khi chơi poker.",
            allowed_mentions=NO_MENTIONS,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokerCog(bot))
