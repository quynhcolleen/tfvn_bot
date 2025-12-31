import discord
from discord.ext import commands
from datetime import datetime

class HeartbeatCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='ping', help='Checks if the bot is responsive.')
    async def ping(self, ctx):
        """Responds with a heartbeat message to confirm the bot is active."""
        await ctx.send("💓 Bot đang hoạt động và phản hồi!")

async def setup(bot: commands.Bot):
    await bot.add_cog(HeartbeatCog(bot))