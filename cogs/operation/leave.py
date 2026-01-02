import discord  # pyright: ignore[reportMissingImports]
import datetime
from discord.ext import commands  # pyright: ignore[reportMissingImports]

class LeaveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="leave", help="Make the bot leave the server.")
    @commands.has_permissions(administrator=True)
    async def leave_guild(self, ctx: commands.Context):
        """Make the bot leave the server."""
        await ctx.send("Tạm biệt mọi người! 👋")
        await ctx.guild.leave()

async def setup(bot: commands.Bot):
    await bot.add_cog(LeaveCog(bot))