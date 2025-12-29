import datetime
import discord
from discord.ext import commands

class TimeoutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Define the command
    @commands.command('timeout')
    @commands.has_permissions(moderate_members=True) # Check if the command author has perms
    async def timeout(self, ctx: commands.Context, member: discord.Member, duration_minutes: str, *, reason: str = "Không có lý do cụ thể"):
        """Times out a user for a specified number of minutes."""

         # Manually convert to int and validate
        try:
            minutes = int(duration_minutes)
        except ValueError:
            await ctx.send("Thời gian timeout phải là một số nguyên dương (ví dụ: 5).")
            return
        
        if minutes <= 0:
            await ctx.send("Thời gian timeout phải lớn hơn 0 phút.")
            return
        
        # Calculate the timeout expiration time
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
        
        try:
            await member.timeout(until, reason=f"{reason} (Requested by {ctx.author.name})")
            await ctx.send(f"Tôi đã timeout {member.mention} trong {duration_minutes} phút. Lý do: {reason}")
        except discord.Forbidden:
            await ctx.send("Tôi không có quyền để timeout thành viên này.")
        except Exception as e:
            await ctx.send(f"Có lỗi đã xảy ra")
    @timeout.error
    async def timeout_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để timeout thành viên.")
    

    @commands.command('untimeout')
    @commands.has_permissions(moderate_members=True) # Check if the command author has perms
    async def untimeout(self, ctx: commands.Context, member: discord.Member):
        """Removes timeout from a user."""
        try:
            await member.timeout(None, reason=f"Timeout removed by {ctx.author.name}")
            await ctx.send(f"Tôi đã gỡ timeout cho {member.mention}.")
        except discord.Forbidden:
            await ctx.send("Tôi không có quyền để gỡ timeout cho thành viên này.")
        except Exception as e:
            await ctx.send(f"Có lỗi đã xảy ra")

    @untimeout.error
    async def untimeout_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để gỡ timeout thành viên.")

async def setup(bot: commands.Bot):
    await bot.add_cog(TimeoutCog(bot))