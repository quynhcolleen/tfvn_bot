from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
import dotenv  # pyright: ignore[reportMissingImports]

dotenv.load_dotenv()

RULE_CHANNEL = int(os.getenv("RULE_CHANNEL"))
ROLE_CHANNEL = int(os.getenv("ROLE_CHANNEL"))
INTRO_CHANNEL = int(os.getenv("INTRO_CHANNEL"))
TEST_CHANNEL = int(os.getenv("TEST_CHANNEL"))
GOODBYE_GIF_URL = os.getenv("GOODBYE_GIF_URL")


class GoodbyeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_goodbye(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title=f"{member.name} đã rời khỏi server 🥹"
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        if GOODBYE_GIF_URL:
            embed.set_image(url=GOODBYE_GIF_URL)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        channel = self.bot.get_channel(INTRO_CHANNEL)
        if channel is None:
            return

        await self.send_goodbye(member, channel)

    # # Test
    # @commands.command(name="test")
    # async def test_goodbye(self, ctx: commands.Context):
    #     channel = self.bot.get_channel(TEST_CHANNEL)
    #     if channel is None:
    #         await ctx.send("❌ Không tìm thấy channel test")
    #         return

    #     await self.send_goodbye(ctx.author, channel)
    #     await ctx.reply("✅ Đã gửi goodbye embed test")

async def setup(bot):
    await bot.add_cog(GoodbyeCog(bot))
