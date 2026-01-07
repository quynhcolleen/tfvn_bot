import discord
from discord.ext import commands

class NicknameCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(
        name="nickchange", help="Changes the nickname of a mentioned member."
    )
    @commands.has_permissions(
        manage_nicknames=True
    )  # Optional: ensures only users with nickname management permission can use the command
    async def change_nickname(self, ctx, member: discord.Member, *, new_nickname: str):
        if not ctx.author.guild_permissions.manage_nicknames:
            await ctx.send("Bạn không có quyền để đổi biệt danh.")
            return

        try:
            # 1. Change the nickname of the member
            await member.edit(nick=new_nickname)

            # 2. Send a confirmation message
            await ctx.send(
                f"Đã đổi biệt danh của {member.mention} thành `{new_nickname}` thành công!"
            )

        except discord.Forbidden:
            await ctx.send(
                "Bot không có quyền cần thiết để đổi biệt danh này. Vui lòng kiểm tra thứ bậc vai trò."
            )
        except Exception as e:
            await ctx.send("Đã xảy ra lỗi không mong muốn")

    @change_nickname.error
    async def change_nickname_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để đổi biệt danh.")

async def setup(bot):
    await bot.add_cog(NicknameCog(bot))