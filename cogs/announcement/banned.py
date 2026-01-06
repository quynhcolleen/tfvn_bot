from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
import os
from assets.gifs import BANNED_GIF

class BannedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        bye_channel_value = self.bot.global_vars["BYE_CHANNEL"]
        try:
            self.bye_channel = int(bye_channel_value)  # Always convert to int
        except ValueError:
            raise ValueError("BYE_CHANNEL must be a valid integer string (e.g., '889516932468973679').")


    async def send_banned(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        embed = discord.Embed(
            title=f"{member.name} đã ăn sút và cút 🔨",
            color=0xFF0000,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        if BANNED_GIF:
            embed.set_image(url=BANNED_GIF)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_ban(self, member: discord.Member):
        channel = self.bot.get_channel(int(self.bye_channels) )  # Use the first channel in the list
        if channel is None:
            return

        await self.send_banned(member, channel)

async def setup(bot):
    await bot.add_cog(BannedCog(bot))
