from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
import dotenv  # pyright: ignore[reportMissingImports]

dotenv.load_dotenv()

BYE_CHANNEL = int(os.getenv("BYE_CHANNEL"))
TEST_CHANNEL = int(os.getenv("TEST_CHANNEL"))
BANNED_GIF_URL = os.getenv("BANNED_GIF_URL")


class BannedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_banned(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title=f"{member.name} đã ăn sút và cút 🔨",
            color=0xFF0000,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        if BANNED_GIF_URL:
            embed.set_image(url=BANNED_GIF_URL)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, member: discord.Member):
        channel = self.bot.get_channel(BYE_CHANNEL)
        if channel is None:
            return

        await self.send_banned(member, channel)

    # # Test
    # @commands.command(name="test")
    # async def test_banned(self, ctx: commands.Context):
    #     channel = self.bot.get_channel(TEST_CHANNEL)
    #     if channel is None:
    #         await ctx.send("❌ Không tìm thấy channel test")
    #         return

    #     await self.send_banned(ctx.author, channel)
    #     await ctx.reply("✅ Đã gửi banned embed test")

async def setup(bot):
    await bot.add_cog(BannedCog(bot))
