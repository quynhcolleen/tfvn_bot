import discord
from discord.ext import commands
import os
import random
import dotenv  # pyright: ignore[reportMissingImports]

dotenv.load_dotenv()

class UserInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="kiss")
    async def kiss(self, ctx, member: discord.Member):
        gif_urls = [
            os.getenv("KISS_GIF_1_URL"),
            os.getenv("KISS_GIF_2_URL"),
            os.getenv("KISS_GIF_3_URL"),
            os.getenv("KISS_GIF_4_URL"),
        ]

        embed = discord.Embed(
          title="💋 Moah moahhh~",
          description=f"{ctx.author.mention} hôn {member.mention} 💖"
        )
        embed.set_image(url=random.choice(gif_urls))

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(UserInteractionCog(bot))
