from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
import dotenv  # pyright: ignore[reportMissingImports]
from assets.gifs import WELCOME_GIF
dotenv.load_dotenv()

RULE_CHANNEL = int(os.getenv("RULE_CHANNEL"))
ROLE_CHANNEL = int(os.getenv("ROLE_CHANNEL"))
JOIN_CHANNEL = int(os.getenv("JOIN_CHANNEL"))
TEST_CHANNEL = int(os.getenv("TEST_CHANNEL"))

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_welcome(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title="🎉 Chào mừng tới Trap & Femboy VN!",
            description=(
                f"Chào mừng {member.mention} đến với **Trap & Femboy VN** nha!\n\n"
                f"📌 Xem luật tại <#{RULE_CHANNEL}>\n"
                f"🍭 Chọn role tại <#{ROLE_CHANNEL}>\n\n"
                "Chúc bạn ngắm femboy vui vẻ nhé! 💗"
            ),
            color=0xFFC0CB,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        if WELCOME_GIF:
            embed.set_image(url=WELCOME_GIF)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self.bot.get_channel(JOIN_CHANNEL)
        if channel is None:
            return

        await self.send_welcome(member, channel)

    ## Test
    # @commands.command(name="test")
    # async def test_welcome(self, ctx: commands.Context):
    #     channel = self.bot.get_channel(TEST_CHANNEL)
    #     if channel is None:
    #         await ctx.send("❌ Không tìm thấy channel test")
    #         return

    #     await self.send_welcome(ctx.author, channel)
    #     await ctx.reply("✅ Đã gửi welcome embed test")


async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
