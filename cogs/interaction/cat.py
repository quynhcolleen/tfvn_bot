import discord
from discord.ext import commands
import aiohttp

CAT_API_URL = "https://api.thecatapi.com/v1/images/search"

class CatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="cat")
    async def cat(self, ctx: commands.Context):
        async with aiohttp.ClientSession() as session:
            async with session.get(CAT_API_URL) as resp:
                if resp.status != 200:
                    await ctx.send("😿 Không lấy được ảnh mèo, thử lại sau nhé!")
                    return

                data = await resp.json()

        image_url = data[0]["url"]

        embed = discord.Embed(
            title="🐱 Meow meow!",
            color=discord.Color.orange()
        )
        embed.set_image(url=image_url)

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(CatCog(bot))
