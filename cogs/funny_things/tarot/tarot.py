"""Interactive tarot readings with face-down spreads and flip controls."""

from __future__ import annotations

import logging
import weakref

import discord
from discord.ext import commands

from cogs.funny_things.tarot._tarot_helpers import (
    Spread,
    deal_reading,
    parse_tarot_query,
)
from cogs.funny_things.tarot._tarot_ui import (
    NO_MENTIONS,
    TarotPickerView,
    TarotReadingView,
)


logger = logging.getLogger(__name__)


class TarotCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._views: weakref.WeakSet[discord.ui.View] = weakref.WeakSet()

    def track(self, view: discord.ui.View) -> None:
        self._views.add(view)

    def cog_unload(self) -> None:
        for view in list(self._views):
            view.stop()

    def _owner_profile(
        self, user: discord.abc.User
    ) -> tuple[int, str, str | None]:
        avatar = getattr(user, "display_avatar", None)
        avatar_url = avatar.url if avatar is not None else None
        display_name = getattr(user, "display_name", user.name)
        return int(user.id), str(display_name), avatar_url

    async def begin_reading(
        self,
        interaction: discord.Interaction,
        *,
        spread: Spread,
        question: str,
        owner_id: int,
        display_name: str,
        avatar_url: str | None,
    ) -> None:
        view = TarotReadingView(
            self,
            reading=deal_reading(spread, question),
            owner_id=owner_id,
            display_name=display_name,
            avatar_url=avatar_url,
        )
        self.track(view)
        try:
            await interaction.response.edit_message(**await view.edit_kwargs())
        except discord.HTTPException:
            logger.exception("Could not open tarot reading from spread picker")
            return
        view.message = interaction.message

    @commands.command(
        name="tarot",
        aliases=("boi",),
        usage="[trải] [câu hỏi]",
        help=(
            "Bói bài Tarot: 1 lá, 3 lá, 5 lá, 7 lá hoặc Celtic Cross 10 lá. "
            "Lật từng lá hoặc lật tất cả."
        ),
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def tarot(self, ctx: commands.Context, *, query: str = "") -> None:
        spread, question = parse_tarot_query(query)
        owner_id, display_name, avatar_url = self._owner_profile(ctx.author)
        if spread is None:
            view = TarotPickerView(
                self,
                owner_id=owner_id,
                display_name=display_name,
                avatar_url=avatar_url,
                question=question,
            )
            self.track(view)
            view.message = await ctx.send(
                embed=view.build_embed(),
                view=view,
                allowed_mentions=NO_MENTIONS,
            )
            return

        view = TarotReadingView(
            self,
            reading=deal_reading(spread, question),
            owner_id=owner_id,
            display_name=display_name,
            avatar_url=avatar_url,
        )
        self.track(view)
        view.message = await ctx.send(**await view.send_kwargs())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TarotCog(bot))
