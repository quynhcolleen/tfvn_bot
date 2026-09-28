"""Interactive slot machine backed by the shared Trap Coin balance."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.minigames._card_game_economy import CardGameBank
from cogs.minigames._casino_ui import (
    BANNER_LOSS,
    BANNER_WIN,
    attach_table_image,
    render_slot_table,
)
from cogs.minigames.slot_machine._slot_helpers import (
    SLOT_COST,
    SLOT_GAME_NAME,
    SLOT_JACKPOT,
    format_reels,
    slot_outcome_text,
    slot_payout,
    spin_reels,
)


logger = logging.getLogger(__name__)

SLOT_TIMEOUT_SECONDS = 90
SLOT_TABLE_FILENAME = "slot.png"
NO_MENTIONS = discord.AllowedMentions.none()


class SlotMachineView(discord.ui.View):
    """Owner-only cabinet with a replay button after each settled spin."""

    def __init__(
        self,
        cog: "SlotMachineCog",
        *,
        user_id: int,
        guild_id: int | None,
        display_name: str,
        session_id: str,
        reels: tuple[str, str, str],
        payout: int,
        balance: int,
    ) -> None:
        super().__init__(timeout=SLOT_TIMEOUT_SECONDS)
        self.cog = cog
        self.user_id = int(user_id)
        self.guild_id = guild_id
        self.display_name = display_name
        self.session_id = session_id
        self.reels = reels
        self.payout = int(payout)
        self.balance = int(balance)
        self.message: discord.Message | None = None
        self._closed = False
        self._action_lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        try:
            await interaction.response.send_message(
                "Chỉ người mở máy slot mới dùng được nút này.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug(
                "Could not reject non-owner slot interaction session=%s",
                self.session_id,
                exc_info=True,
            )
        return False

    def _disable_controls(self) -> None:
        for item in self.children:
            item.disabled = True

    def _banner(self) -> tuple[str, tuple[int, int, int]]:
        if self.payout == SLOT_JACKPOT:
            return "NỔ HŨ!", BANNER_WIN
        if self.payout > 0:
            return "Thắng cặp!", BANNER_WIN
        return "Trượt mất rồi.", BANNER_LOSS

    def build_embed(self) -> discord.Embed:
        status, _fill = self._banner()
        if self.payout > 0:
            description = (
                f"Người chơi: **{self.display_name}**\n"
                f"{format_reels(self.reels)}\n\n"
                f"🎉 **{slot_outcome_text(self.payout)}** "
                f"Nhận **{self.payout:,} TC**."
            )
            color = discord.Color.green()
        else:
            description = (
                f"Người chơi: **{self.display_name}**\n"
                f"{format_reels(self.reels)}\n\n"
                f"💀 **{slot_outcome_text(self.payout)}**"
            )
            color = discord.Color.red()
        embed = discord.Embed(title="🎰 Máy slot", description=description, color=color)
        embed.set_footer(text=f"Số dư: {self.balance:,} TC · {status}")
        return embed

    def _render_table(self) -> bytes:
        status, fill = self._banner()
        return render_slot_table(
            reels=self.reels,
            bet=SLOT_COST,
            balance=self.balance,
            status=status,
            banner_fill=fill,
        )

    async def _table_png(self) -> bytes | None:
        try:
            return await asyncio.to_thread(self._render_table)
        except Exception:
            logger.exception(
                "Could not render slot table session=%s",
                self.session_id,
            )
            return None

    async def _send_kwargs(self) -> dict:
        embed = self.build_embed()
        extra = attach_table_image(
            embed,
            await self._table_png(),
            SLOT_TABLE_FILENAME,
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
            SLOT_TABLE_FILENAME,
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
                "Could not update slot interaction session=%s",
                self.session_id,
            )

    async def _safe_message_edit(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.edit(**await self._edit_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not edit slot message session=%s",
                self.session_id,
            )

    def close(self) -> None:
        self._closed = True
        self._disable_controls()
        self.cog.unregister(self)
        self.stop()

    async def on_timeout(self) -> None:
        async with self._action_lock:
            if self._closed:
                return
            self.close()
        await self._safe_message_edit()

    @discord.ui.button(
        label="Quay lại",
        emoji="🎰",
        style=discord.ButtonStyle.success,
    )
    async def spin_again_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self._action_lock.locked():
            try:
                await interaction.response.send_message(
                    "Máy đang quay, chờ một chút nhé.",
                    ephemeral=True,
                    allowed_mentions=NO_MENTIONS,
                )
            except discord.HTTPException:
                pass
            return

        async with self._action_lock:
            if self._closed:
                try:
                    await interaction.response.send_message(
                        "Máy slot này đã đóng.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return

            session_id = uuid.uuid4().hex
            try:
                settled = self.cog.play_round(
                    user_id=self.user_id,
                    guild_id=self.guild_id,
                    session_id=session_id,
                )
            except PyMongoError:
                logger.exception(
                    "Could not settle slot replay session=%s user=%s",
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

            if settled is None:
                try:
                    await interaction.response.send_message(
                        f"Bạn không có đủ **{SLOT_COST:,} TC** để quay tiếp.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                    )
                except discord.HTTPException:
                    pass
                return

            self.session_id = session_id
            self.reels, self.payout, self.balance = settled
            await self._safe_interaction_edit(interaction)


class SlotMachineCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.bank = CardGameBank(bot.db)
        self.active_sessions: dict[int, SlotMachineView] = {}
        self._starting_users: set[int] = set()
        self._unloading = False

    def unregister(self, view: SlotMachineView) -> None:
        if self.active_sessions.get(view.user_id) is view:
            self.active_sessions.pop(view.user_id, None)

    def cog_unload(self) -> None:
        self._unloading = True
        for view in list(self.active_sessions.values()):
            view.close()

    def play_round(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        session_id: str,
        rng: Any = None,
    ) -> tuple[tuple[str, str, str], int, int] | None:
        """Reserve the stake, spin, credit any win, and return the settled result."""

        balance = self.bank.reserve_wager(
            user_id,
            guild_id,
            SLOT_GAME_NAME,
            SLOT_COST,
            session_id,
        )
        if balance is None:
            return None
        reels = spin_reels(rng)
        payout = slot_payout(reels)
        if payout > 0:
            balance = self.bank.credit(
                user_id,
                guild_id,
                SLOT_GAME_NAME,
                payout,
                session_id,
                "win",
            )
        return reels, payout, balance

    async def _send_plain(self, ctx: commands.Context, content: str) -> None:
        await ctx.send(content, allowed_mentions=NO_MENTIONS)

    @commands.command(
        name="slot",
        help="Quay máy slot bằng Trap Coin.",
        usage="slot",
    )
    async def slot(self, ctx: commands.Context) -> None:
        user_id = int(ctx.author.id)
        guild_id = ctx.guild.id if ctx.guild is not None else None
        if self._unloading:
            await self._send_plain(
                ctx, "Máy slot đang tải lại, bạn thử lại sau một chút nhé."
            )
            return
        if user_id in self._starting_users or user_id in self.active_sessions:
            await self._send_plain(
                ctx,
                "Bạn đang có một máy slot đang mở. Hãy quay xong hoặc đợi máy đóng.",
            )
            return

        self._starting_users.add(user_id)
        session_id = uuid.uuid4().hex
        try:
            try:
                settled = self.play_round(
                    user_id=user_id,
                    guild_id=guild_id,
                    session_id=session_id,
                )
            except PyMongoError:
                logger.exception(
                    "Could not reserve slot wager session=%s user=%s",
                    session_id,
                    user_id,
                )
                await self._send_plain(
                    ctx, "Không thể trừ tiền cược lúc này. Vui lòng thử lại."
                )
                return

            if settled is None:
                await self._send_plain(
                    ctx,
                    f"Bạn không có đủ **{SLOT_COST:,} TC** để chơi slot.",
                )
                return

            reels, payout, balance = settled
            view = SlotMachineView(
                self,
                user_id=user_id,
                guild_id=guild_id,
                display_name=ctx.author.display_name,
                session_id=session_id,
                reels=reels,
                payout=payout,
                balance=balance,
            )
            if self._unloading:
                view.close()
                return
            self.active_sessions[user_id] = view
        finally:
            self._starting_users.discard(user_id)

        try:
            view.message = await ctx.send(**await view._send_kwargs())
        except discord.HTTPException:
            logger.exception(
                "Could not send slot cabinet session=%s user=%s",
                session_id,
                user_id,
            )
            view.close()
            return
        except Exception:
            logger.exception(
                "Unexpected failure sending slot cabinet session=%s user=%s",
                session_id,
                user_id,
            )
            view.close()
            raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlotMachineCog(bot))
