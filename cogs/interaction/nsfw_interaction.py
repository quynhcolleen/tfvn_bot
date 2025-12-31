import asyncio
import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import random
from collections import deque

from assets.nsfw_gifs import (
    BLOWJOB_GIFS,
    HANDJOB_GIFS,
    FROTTING_GIFS,
    FUCKING_GIFS,
    CREAMPIE_GIFS,
)


# Tránh lặp gif đcmmmmmmm
class GifPicker:
    def __init__(self, gifs: list[str], history_size: int = 5):
        self.gifs = gifs
        self.recent = deque(maxlen=history_size)

    def pick(self) -> str:
        candidates = [g for g in self.gifs if g not in self.recent]
        gif = random.choice(candidates or self.gifs)
        self.recent.append(gif)
        return gif


class NSFWInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bj_picker = GifPicker(BLOWJOB_GIFS, history_size=8)
        self.hj_picker = GifPicker(HANDJOB_GIFS, history_size=7)
        self.frot_picker = GifPicker(FROTTING_GIFS, history_size=5)
        self.fuck_picker = GifPicker(FUCKING_GIFS, history_size=15)
        self.cream_picker = GifPicker(CREAMPIE_GIFS, history_size=5)

    async def _nsfw_guard(self, ctx: commands.Context) -> bool:
        if ctx.channel.is_nsfw():
            return True

        await ctx.message.add_reaction("⚠️")
        warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
        await asyncio.sleep(5)
        await warn_msg.delete()
        await ctx.message.delete()
        return False

    async def _send_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        gif_url: str,
    ):
        embed = discord.Embed(
            title=title,
            description=description,
        )
        embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    # BLOWJOB
    @commands.command(name="bj")
    async def blowjob(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        await self._send_embed(
            ctx,
            title="👅 Bú bú",
            description=f"{ctx.author.mention} bú cu {member.mention} 💖",
            gif_url=self.bj_picker.pick(),
        )

    # HANDJOB
    @commands.command(name="hj")
    async def handjob(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        await self._send_embed(
            ctx,
            title="🥰 Sục cho nè~",
            description=f"{ctx.author.mention} sục cho {member.mention} 💦",
            gif_url=self.hj_picker.pick(),
        )
        
    # FROTTING
    @commands.command(name="frot")
    async def frotting(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        await self._send_embed(
            ctx,
            title="🤺 Đấu kiếm nhẹ nhàng nha~",
            description=f"{ctx.author.mention} frot với {member.mention} 🌸",
            gif_url=self.frot_picker.pick(),
        )

    # FUCKING
    @commands.command(name="fuck")
    async def fucking(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        await self._send_embed(
            ctx,
            title="Lên giường thôi 👉🏻👌🏻💦",
            description=f"{ctx.author.mention} chịch {member.mention} 💦",
            gif_url=self.fuck_picker.pick(),
        )

    # CREAMPIE
    @commands.command(name="cream")
    async def creampie(self, ctx: commands.Context, member: discord.Member):
        if not await self._nsfw_guard(ctx):
            return

        await self._send_embed(
            ctx,
            title="💦 Aaaahhh~! Em chịu không nổi nữa rồi...",
            description=f"{ctx.author.mention} ra bên trong {member.mention} 💦!",
            gif_url=self.cream_picker.pick(),
        )
        
async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWInteractionCog(bot))
