from discord.ext import commands  # pyright: ignore[reportMissingImports]
import os  # pyright: ignore[reportMissingImports]
import dotenv  # pyright: ignore[reportMissingImports]

dotenv.load_dotenv()


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.invite_link = os.getenv("INVITE_LINK")
        
    @commands.command()
    async def hello(self, ctx):
        await ctx.reply(f"Xin chào {ctx.author.mention}! Chúc bạn một ngày vui vẻ!")

    @commands.command()
    async def invite(self, ctx):
        await ctx.reply(self.invite_link)
        
    @commands.command()
    async def verify(self, ctx):
        await ctx.reply(f"Hãy vào xem <#{os.getenv('VERIFY_CHANNEL')}> nhé!")


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
