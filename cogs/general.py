from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def hello(self, ctx):
        await ctx.send(f"Xin chào {ctx.author.mention}! Chúc bạn một ngày vui vẻ!")
    
    
async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
