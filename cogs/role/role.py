import discord
from discord.ext import commands
from discord.utils import get


class RollCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='roleroll', help='Gives a specified role to a mentioned member.')
    @commands.has_permissions(manage_roles=True) # Optional: ensures only users with role management permission can use the command
    
    async def give_role(self, ctx, member: discord.Member, *, role_name: str):
        """
        Assigns a role to a member. The bot needs manage_roles permission and
        must be higher in hierarchy than the role it's assigning.
        """

        """
        check if user has permission to manage roles
        """
        if not ctx.author.guild_permissions.manage_roles:
            await ctx.send("Bạn không có quyền để gán vai trò.")
            return
        
        try:
            # 1. Find the role object by its name in the guild (server)
            role = get(ctx.guild.roles, name=role_name)

            if role is None:
                await ctx.send(f"Xin lỗi, mình không tìm thấy vai trò `{role_name}`.")
                return

            # 2. Check if the member already has the role (optional, for clean messaging)
            if role in member.roles:
                await ctx.send(f"{member.mention} đã có vai trò `{role_name}` rồi.")
                return

            # 3. Add the role to the member
            await member.add_roles(role)

            # 4. Send a confirmation message
            await ctx.send(f"Đã gán vai trò `{role.name}` cho {member.mention} thành công!")

        except discord.Forbidden:
            await ctx.send("Mình không có quyền cần thiết để gán vai trò này. Vui lòng kiểm tra thứ bậc vai trò và quyền của mình.")
        except Exception as e:
            await ctx.send(f"Đã xảy ra lỗi không mong muốn")

    @give_role.error
    async def give_role_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để gán vai trò.")

    @commands.command(name='roleunroll', help='Removes a specified role from a mentioned member.')
    @commands.has_permissions(manage_roles=True)
    async def remove_role(self, ctx, member: discord.Member, *, role_name: str):
        """
        Removes a role from a member. The bot needs manage_roles permission and
        must be higher in hierarchy than the role it's removing.
        """

        """
        check if user has permission to manage roles
        """
        if not ctx.author.guild_permissions.manage_roles:
            await ctx.send("Bạn không có quyền để gỡ vai trò.")
            return
        
        try:
            # 1. Find the role object by its name in the guild (server)
            role = get(ctx.guild.roles, name=role_name)

            if role is None:
                await ctx.send(f"Xin lỗi, mình không tìm thấy vai trò `{role_name}`.")
                return

            # 2. Check if the member actually has the role (optional, for clean messaging)
            if role not in member.roles:
                await ctx.send(f"{member.mention} không có vai trò `{role_name}`.")
                return

            # 3. Remove the role from the member
            await member.remove_roles(role)

            # 4. Send a confirmation message
            await ctx.send(f"Đã gỡ vai trò `{role.name}` khỏi {member.mention} thành công!")

        except discord.Forbidden:
            await ctx.send("Mình không có quyền cần thiết để gỡ vai trò này. Vui lòng kiểm tra thứ bậc vai trò và quyền của mình.")
        except Exception as e:
            await ctx.send(f"Đã xảy ra lỗi không mong muốn")
    
    @remove_role.error
    async def remove_role_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("Bạn không có quyền để gỡ vai trò.")
        
async def setup(bot: commands.Bot):
    await bot.add_cog(RollCog(bot))