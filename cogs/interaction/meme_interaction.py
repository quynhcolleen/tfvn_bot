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
        gif_url = random.choice(GIFS_36)
        
        # Check if it's a local file
        if gif_url.startswith("$local::"):
            file_path = gif_url.replace("$local::", "", 1)
            file = discord.File(file_path, filename="interaction.gif")
            embed.set_image(url="attachment://interaction.gif")
            await ctx.send(embed=embed, file=file)
        else:
            embed.set_image(url=gif_url)
            await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemeCog(bot))
