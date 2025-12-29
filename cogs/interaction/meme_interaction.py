import discord
from discord.ext import commands
import random
from assets.gifs import (GIFS_36, )

class MemeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="36")
    async def meme36(self, ctx):
        embed = discord.Embed(title="36")
        embed.set_image(url=random.choice(GIFS_36))

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemeCog(bot))
