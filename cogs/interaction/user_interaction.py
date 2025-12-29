import discord
from discord.ext import commands
import random

from assets.gifs import (
    KISS_GIFS,
    HUG_GIFS,
    PAT_GIFS,
    POKE_GIFS,
    PUNCH_GIFS,
    SLAP_GIFS,
)

HIT_GIFS = SLAP_GIFS + PUNCH_GIFS

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
            description=f"{ctx.author.mention} tát {member.mention} 🤚🏻",
        )
        embed.set_image(url=random.choice(SLAP_GIFS))
        await ctx.send(embed=embed)

    # Punch
    @commands.command(name="punch")
    async def punch(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="👊 Một đấm là nằm!",
            description=f"{ctx.author.mention} đấm {member.mention} 👊🏻",
        )
        embed.set_image(url=random.choice(PUNCH_GIFS))
        await ctx.send(embed=embed)

    # Hit (Slap or Punch)
    @commands.command(name="hit")
    async def hit(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="💥 Bốp bốp!",
            description=f"{ctx.author.mention} đánh {member.mention} 🔨",
        )
        embed.set_image(url=random.choice(HIT_GIFS))
        await ctx.send(embed=embed)
        
    # Poke
    @commands.command(name="poke")
    async def poke(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="👉 Chọc chọc!",
            description=f"{ctx.author.mention} chọc {member.mention} 👉🏻",
        )
        embed.set_image(url=random.choice(POKE_GIFS))
        await ctx.send(embed=embed)
            
async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
