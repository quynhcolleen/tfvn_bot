import discord
from discord.ext import commands

class BanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='ban', help='Bans a specified member from the server.')
    @commands.has_permissions(ban_members=True)  # Ensure only users with ban permissions can
    async def ban_member(self, ctx, member: discord.Member, *, reason: str = "Không có lý do cụ thể"):
        """
        Bans a member from the server. The bot needs ban_members permission and
        must be higher in hierarchy than the member it's banning.
        """

        # 1. Check if the command author has permission to ban members
        if not ctx.author.guild_permissions.ban_members:
            await ctx.send("Bạn không có quyền để cấm thành viên.")
            return

        try:
            # 2. Ban the member
            await member.ban(reason=f"{reason} (Requested by {ctx.author.name})")

            # 3. Send a confirmation message
            await ctx.send(f"Đã cấm {member.mention} khỏi server. Lý do: {reason}")

        except discord.Forbidden:
            await ctx.send("Mình không có quyền cần thiết để cấm thành viên này. Vui lòng kiểm tra thứ bậc vai trò và quyền của mình.")
        except Exception as e:
            await ctx.send(f"Đã xảy ra lỗi không mong muốn")

    @ban_member.error
    async def ban_member_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để cấm thành viên.")

# async def setup(bot: commands.Bot):
#     await bot.add_cog(BanCog(bot))