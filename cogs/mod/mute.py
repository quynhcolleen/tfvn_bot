import discord
import datetime
from discord.ext import commands

class MuteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="mute")
    @commands.has_permissions(manage_roles=True)
    async def mute_member(self, ctx: commands.Context, member: discord.Member):
        """Mute a member for a specified duration in minutes."""
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not mute_role:
            mute_role = await ctx.guild.create_role(name="Muted")

            for channel in ctx.guild.channels:
                await channel.set_permissions(mute_role, speak=False, send_messages=False, read_message_history=True, read_messages=False)

        await member.add_roles(mute_role)
        await ctx.send(f"{member.mention} đã bị mute.")

    @commands.command(name="unmute")
    @commands.has_permissions(manage_roles=True)
    async def unmute_member(self, ctx: commands.Context, member: discord.Member):
        """Unmute a member."""
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if mute_role in member.roles:
            await member.remove_roles(mute_role)
            await ctx.send(f"{member.mention} đã được unmute.")
        else:
            await ctx.send(f"{member.mention} không bị mute.")

async def setup(bot: commands.Bot):
    await bot.add_cog(MuteCog(bot))

