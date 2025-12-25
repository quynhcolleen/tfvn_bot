from discord.ext import commands
import discord

RULE_CHANNEL = 890635313590992916
ROLE_CHANNEL = 889515119829200926
INTRO_CHANNEL = 889523103909167114

class MemberPresenceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        channel = discord.utils.get(guild.text_channels, name="welcome")
        if channel:
            await channel.send(f"Chào mừng {member.mention} đã gia nhập {guild.name}!")


async def setup(bot):
    await bot.add_cog(MemberPresenceCog(bot))
