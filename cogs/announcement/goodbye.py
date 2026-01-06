from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
from assets.gifs import GOODBYE_GIF

class GoodbyeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if (self.bot.global_vars.get("BYE_CHANNEL") is None or self.bot.global_vars.get("BYE_CHANNEL") == ""):
            raise ValueError("BYE_CHANNEL is not set in global variables.")
        
        bye_channel_value = self.bot.global_vars["BYE_CHANNEL"]
        try:
            self.bye_channel = int(bye_channel_value)  # Always convert to int
        except ValueError:
            raise ValueError("BYE_CHANNEL must be a valid integer string (e.g., '889516932468973679').")

        print("GoodbyeCog initialized with channel:", self.bye_channel, "Type:", type(self.bye_channel))  # Debug log

    async def send_goodbye(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title=f"{member.name} đã rời khỏi server 🥹"
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        if GOODBYE_GIF:
            embed.set_image(url=GOODBYE_GIF)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        print("Member left:", member.name)  # Debug log
        channel = self.bot.get_channel(self.bye_channel)

        print(self.bye_channel)
        print ("Goodbye channel:", channel)  # Debug log
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
