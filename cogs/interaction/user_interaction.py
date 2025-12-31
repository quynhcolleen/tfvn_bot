import random
from collections import deque
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from assets.gifs import (
    KISS_GIFS,
    HUG_GIFS,
    PAT_GIFS,
    POKE_GIFS,
    PUNCH_GIFS,
    SLAP_GIFS,
)

HIT_GIFS = SLAP_GIFS + PUNCH_GIFS


# tránh lặp lại gif gần đây
class GifPicker:
    def __init__(self, gifs: list[str], history_size: int = 5):
        self.gifs = gifs
        self.recent = deque(maxlen=history_size)

    def pick(self) -> str:
        candidates = [g for g in self.gifs if g not in self.recent]
        gif = random.choice(candidates or self.gifs)
        self.recent.append(gif)
        return gif


class UserInteractionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.kiss_picker = GifPicker(KISS_GIFS, history_size=5)
        self.hug_picker = GifPicker(HUG_GIFS, history_size=5)
        self.pat_picker = GifPicker(PAT_GIFS, history_size=5)
        self.slap_picker = GifPicker(SLAP_GIFS, history_size=5)
        self.punch_picker = GifPicker(PUNCH_GIFS, history_size=5)
        self.hit_picker = GifPicker(HIT_GIFS, history_size=5)
        self.poke_picker = GifPicker(POKE_GIFS, history_size=5)

    # gọn gọn send embed
    async def _send_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        gif_url: str | None = None,
    ):
        embed = discord.Embed(title=title, description=description)
        if gif_url:
            embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    # KISS
    @commands.command(name="kiss")
    async def kiss(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="💋 Moah moahhh~",
            description=f"{ctx.author.mention} hôn {member.mention} 💖",
            gif_url=self.kiss_picker.pick(),
        )

    # HUG
    @commands.command(name="hug")
    async def hug(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="🤗 Ỏoooo, ôm cái nào!",
            description=f"{ctx.author.mention} ôm {member.mention} 🫂",
            gif_url=self.hug_picker.pick(),
        )

    # PAT
    @commands.command(name="pat")
    async def pat(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="😉 Xoa đầu cái nha~",
            description=f"{ctx.author.mention} xoa đầu {member.mention} 🌸",
            gif_url=self.pat_picker.pick(),
        )

    # SLAP
    @commands.command(name="slap")
    async def slap(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="🤬 Ăn tát đi!",
            description=f"{ctx.author.mention} tát {member.mention} 🤚🏻",
            gif_url=self.slap_picker.pick(),
        )

    # PUNCH
    @commands.command(name="punch")
    async def punch(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="👊 Một đấm là nằm!",
            description=f"{ctx.author.mention} đấm {member.mention} 👊🏻",
            gif_url=self.punch_picker.pick(),
        )

    # HIT
    @commands.command(name="hit")
    async def hit(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="💥 Bốp bốp!",
            description=f"{ctx.author.mention} đánh {member.mention} 🔨",
            gif_url=self.hit_picker.pick(),
        )

    # POKE
    @commands.command(name="poke")
    async def poke(self, ctx: commands.Context, member: discord.Member):
        await self._send_embed(
            ctx,
            title="👉 Chọc chọc!",
            description=f"{ctx.author.mention} chọc {member.mention} 👉🏻",
            gif_url=self.poke_picker.pick(),
        )

    # AVATAR
    @commands.command(name="avatar", aliases=["av"])
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None):
        member = member or ctx.author
        await self._send_embed(
            ctx,
            title=f"📸 Avatar của {member.name}:",
            description="",
            gif_url=member.avatar.url,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
