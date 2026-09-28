from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]
from assets.gifs import WELCOME_GIF
from cogs.announcement._media import announcement_gif_url
from cogs.settings._guild_variables import get_guild_variable

class WelcomeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _configured_channel(self, guild: discord.Guild, name: str):
        try:
            channel_id = int(get_guild_variable(self.bot, guild.id, name))
        except (TypeError, ValueError):
            return None
        channel = self.bot.get_channel(channel_id)
        if getattr(getattr(channel, "guild", None), "id", None) != guild.id:
            return None
        return channel

    async def send_welcome(
        self, member: discord.abc.User, channel: discord.TextChannel
    ):
        rule_channel = self._configured_channel(channel.guild, "RULE_CHANNEL")
        role_channel = self._configured_channel(channel.guild, "ROLE_CHANNEL")
        if rule_channel is None or role_channel is None:
            return
        embed = discord.Embed(
            title="🎉 Chào mừng tới Trap & Femboy VN!",
            description=(
                f"Chào mừng {member.mention} đến với **Trap & Femboy VN** nha!\n\n"
                f"📌 Xem luật tại <#{rule_channel.id}>\n"
                f"🍭 Chọn role tại <#{role_channel.id}>\n\n"
                "Chúc bạn ngắm femboy vui vẻ nhé! 💗"
            ),
            color=0xFFC0CB,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)

        embed.set_thumbnail(url=member.display_avatar.url)

        image_url = announcement_gif_url(
            self.bot, channel.guild.id, "WELCOME_GIF_URL", WELCOME_GIF
        )
        if image_url:
            embed.set_image(url=image_url)

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        channel = self._configured_channel(member.guild, "JOIN_CHANNEL")

        if channel is None:
            return

        await self.send_welcome(member, channel)

async def setup(bot):
    await bot.add_cog(WelcomeCog(bot))
