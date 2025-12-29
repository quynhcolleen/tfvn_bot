import discord
from discord.ext import commands

class KickCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='kick', help='Kicks a specified member from the server.')
    @commands.has_permissions(kick_members=True)  # Ensure only users with kick permissions can
    async def kick_member(self, ctx, member: discord.Member, *, reason: str = "Không có lý do cụ thể"):
        """
        Kicks a member from the server. The bot needs kick_members permission and
        must be higher in hierarchy than the member it's kicking.
        """

        # 1. Check if the command author has permission to kick members
        if not ctx.author.guild_permissions.kick_members:
            await ctx.send("Bạn không có quyền để đá thành viên.")
            return

        try:
            # 2. Kick the member
            await member.kick(reason=f"{reason} (Requested by {ctx.author.name})")

            # 3. Send a confirmation message
            await ctx.send(f"Đã đá {member.mention} khỏi server. Lý do: {reason}")

        except discord.Forbidden:
            await ctx.send("Mình không có quyền cần thiết để đá thành viên này. Vui lòng kiểm tra thứ bậc vai trò và quyền của mình.")
        except Exception as e:
            await ctx.send(f"Đã xảy ra lỗi không mong muốn")

    @kick_member.error
    async def kick_member_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để đá thành viên.")

# async def setup(bot: commands.Bot):
#     await bot.add_cog(KickCog(bot))