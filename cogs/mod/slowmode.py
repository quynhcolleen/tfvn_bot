import discord
from discord.ext import commands
import datetime

class SlowmodeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="slowmode", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def slowmode(self, ctx: commands.Context):
        """ put the guide for slowmode here """
        await ctx.send_help(ctx.command)

    @slowmode.command(name="check_bypass")
    async def check_slowmode_bypass(self, ctx: commands.Context):
        # Get the permissions for the command author in the current channel
        perms = ctx.channel.permissions_for(ctx.author)

        # Check for the specific permissions that grant bypass
        # The 'manage_messages' attribute is the name used in discord.py for the permission
        print(perms.manage_messages, perms.manage_channels, perms.bypass_slowmode)
        if perms.manage_messages or perms.manage_channels or perms.bypass_slowmode: # 'bypass_slowmode' is for the newer dedicated permission
            await ctx.send(f"{ctx.author.mention} có thể bypass chế độ chậm trong kênh này.")
        else:
            await ctx.send(f"{ctx.author.mention} không thể bypass chế độ chậm trong kênh này và phải tuân theo nó.")
    @check_slowmode_bypass.error
    async def check_slowmode_bypass_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền sử dụng lệnh này.")
        else:
            await ctx.send("Đã xảy ra lỗi khi thực hiện lệnh kiểm tra bypass chế độ chậm.")
            raise error

    @slowmode.command(name="immune")
    async def slowmode_immune(self, ctx: commands.Context, member: discord.Member):
        print(f"Setting slowmode immunity for {member} in channel {ctx.channel}")
        try:
            await ctx.channel.set_permissions(
                member,
                overwrite=discord.PermissionOverwrite(
                    send_messages=True,
                    # bypass_slowmode=True
                ),
                reason="Slowmode immunity (this channel only)"
            )
        except Exception as e:
            await ctx.send(f"Đã xảy ra lỗi khi cố gắng thiết lập miễn chế độ chậm: Hiện tại discord không cho phép thiết lập quyền này.")
            await ctx.send(f"Latest discord.py (2.6.4, Oct 2025) does not implement Discord's new BYPASS_SLOWMODE (added Nov 2025). Bypass still via manage_channels or manage_messages")
            return

        await ctx.send(f"{member.mention} đã được miễn chế độ chậm **trong kênh này**.")
    @slowmode_immune.error
    async def slowmode_immune_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền sử dụng lệnh này.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("Thành viên không hợp lệ. Vui lòng đề cập đến một thành viên hợp lệ.")
        else:
            await ctx.send("Đã xảy ra lỗi khi thực hiện lệnh slowmode immune.")
            raise error
    
    @slowmode.command(name="prominent")
    async def slowmode_prominent(self, ctx: commands.Context, member: discord.Member):
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(f"{member.mention} đã bị gỡ bỏ miễn chế độ chậm trong kênh này.")
        
async def setup(bot: commands.Bot):
    await bot.add_cog(SlowmodeCog(bot))