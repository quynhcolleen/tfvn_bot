import discord
from discord.ext import commands
import aiohttp

DOG_API_URL = "https://api.thedogapi.com/v1/images/search"

class DogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="dog")
    async def dog(self, ctx: commands.Context):
        async with aiohttp.ClientSession() as session:
            async with session.get(DOG_API_URL) as resp:
                if resp.status != 200:
                    await ctx.send("🐶 Không lấy được ảnh chó, thử lại sau nhé!")
                    return

                data = await resp.json()

        image_url = data[0]["url"]

        embed = discord.Embed(
            title="🐶 Woof woof!",
            color=discord.Color.pink()
        )
        embed.set_image(url=image_url)

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(DogCog(bot))
