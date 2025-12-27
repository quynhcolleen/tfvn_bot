import discord
from discord.ext import commands
import os
import random
import dotenv  # pyright: ignore[reportMissingImports]

dotenv.load_dotenv()

class MemeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="36")
    async def meme36(self, ctx):
        gif_urls = [
            os.getenv("36_1_GIF_URL"),
            os.getenv("36_2_GIF_URL"),
            os.getenv("36_3_GIF_URL"),
            os.getenv("36_4_GIF_URL"),
            os.getenv("36_5_GIF_URL"),
            os.getenv("36_6_GIF_URL"),
        ]

        embed = discord.Embed(title="36")
        embed.set_image(url=random.choice(gif_urls))

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(MemeCog(bot))
