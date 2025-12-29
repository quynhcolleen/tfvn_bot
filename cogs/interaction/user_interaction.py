import discord
from discord.ext import commands
import random

from assets.gifs import (
    KISS_GIFS,
    HUG_GIFS,
    PAT_GIFS,
    SLAP_GIFS,
)


class UserInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Kiss
    @commands.command(name="kiss")
    async def kiss(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="💋 Moah moahhh~",
            description=f"{ctx.author.mention} hôn {member.mention} 💖",
        )
        embed.set_image(url=random.choice(KISS_GIFS))
        await ctx.send(embed=embed)

    # Hug
    @commands.command(name="hug")
    async def hug(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="🤗 Ỏoooo, ôm cái nào!",
            description=f"{ctx.author.mention} ôm {member.mention} 🫂",
        )
        embed.set_image(url=random.choice(HUG_GIFS))
        await ctx.send(embed=embed)

    # Pat
    @commands.command(name="pat")
    async def pat(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="😉 Xoa đầu cái nha~",
            description=f"{ctx.author.mention} xoa đầu {member.mention} 🌸",
        )
        embed.set_image(url=random.choice(PAT_GIFS))
        await ctx.send(embed=embed)

    # Slap
    @commands.command(name="slap")
    async def slap(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="🤬 Ăn tát đi!",
            description=f"{ctx.author.mention} tát {member.mention} !!",
        )
        embed.set_image(url=random.choice(SLAP_GIFS))
        await ctx.send(embed=embed)

    # # Punch
    # @commands.command(name="punch")
    # async def punch(self, ctx, member: discord.Member):
    #     embed = discord.Embed(
    #         title="👊 Đấm là nằm!",
    #         description=f"{ctx.author.mention} đấm {member.mention} !!",
    #     )
    #     embed.set_image(url=random.choice(PUNCH_GIFS))
    #     await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
