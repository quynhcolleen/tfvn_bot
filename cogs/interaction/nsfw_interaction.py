import asyncio
import discord # pyright: ignore[reportMissingImports]
from discord.ext import commands # pyright: ignore[reportMissingImports]
import random

from assets.nsfw_gifs import (
    BLOWJOB_GIFS,
    HANDJOB_GIFS,
    FROTTING_GIFS,
    FUCKING_GIFS,
    CREAMPIE_GIFS
)

class NSFWInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Blowjob
    
    @commands.command(name="bj")
    async def blowjob(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="👅 Bú bú~",
            description=f"{ctx.author.mention} bú cu {member.mention} 💖",
        )
        embed.set_image(url=random.choice(BLOWJOB_GIFS))
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.send(embed=embed)

    # Handjob
    @commands.command(name="hj")
    async def handjob(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="🥰 Sục cho nè~",
            description=f"{ctx.author.mention} sục cho {member.mention} 💦",
        )
        embed.set_image(url=random.choice(HANDJOB_GIFS))
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.send(embed=embed)

    # Frotting
    @commands.command(name="frot")
    async def frotting(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="🤺 Đấu kiếm nhẹ nhàng nha~",
            description=f"{ctx.author.mention} frot với {member.mention} 🌸",
        )
        embed.set_image(url=random.choice(FROTTING_GIFS))
        await ctx.send(embed=embed)

    # Fucking
    @commands.command(name="fuck")
    async def fucking(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="Lên giường thôi 🍆",
            description=f"{ctx.author.mention} chịch {member.mention} 💦",
        )
        embed.set_image(url=random.choice(FUCKING_GIFS))
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.send(embed=embed)

    # Creampie
    @commands.command(name="cream")
    async def creampie(self, ctx, member: discord.Member):
        embed = discord.Embed(
            title="Aaaahhh~! Em chịu không nổi nữa rồi...",
            description=f"{ctx.author.mention} ra bên trong {member.mention} 💦!",
        )
        embed.set_image(url=random.choice(CREAMPIE_GIFS))
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.send(embed=embed)

            
async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWInteractionCog(bot))
