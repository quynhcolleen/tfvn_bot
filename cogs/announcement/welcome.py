from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
from assets.gifs import WELCOME_GIF

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        join_channel_value = self.bot.global_vars["JOIN_CHANNEL"]
        try:
            self.join_channel = int(join_channel_value)  # Always convert to int
        except ValueError:
            raise ValueError("JOIN_CHANNEL must be a valid integer string (e.g., '889516932468973679').")

        rule_channel_value = self.bot.global_vars["RULE_CHANNEL"]
        try:
            self.rule_channel = int(rule_channel_value)  # Always convert to int
        except ValueError:
            raise ValueError("RULE_CHANNEL must be a valid integer string (e.g., '889516932468973679').")

        role_channel_value = self.bot.global_vars["ROLE_CHANNEL"]
        try:
            self.role_channel = int(role_channel_value)  # Always convert to int
        except ValueError:
            raise ValueError("ROLE_CHANNEL must be a valid integer string (e.g., '889516932468973679').")

    async def send_welcome(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title="🎉 Chào mừng tới Trap & Femboy VN!",
            description=(
                f"Chào mừng {member.mention} đến với **Trap & Femboy VN** nha!\n\n"
                f"📌 Xem luật tại <#{self.rule_channel}>\n"
                f"🍭 Chọn role tại <#{self.role_channel}>\n\n"
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
        channel = self.bot.get_channel(self.join_channel)

        print("Member joined:", member.name)  # Debug log

        if channel is None:
            return

        await self.send_welcome(member, channel)

async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
