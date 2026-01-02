import discord
import datetime
from discord.ext import commands

class SoftbanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db 

    def save_old_roles(self, member_id: int, roles: list[int]):
        """ Save old roles to database """
        document = {"member_id": member_id, "old_roles": roles}
        self.db["old_roles"].update_one(
            {"member_id": member_id},
            {"$set": document},
            upsert=True
        )

    def get_old_roles(self, member_id: int) -> list[int] | None:
        """ Retrieve old roles from database """
        document = self.db["old_roles"].find_one({"member_id": member_id})
        if document:
            return document.get("old_roles", [])
        return None

    @commands.command(name="softban")
    @commands.has_permissions(ban_members=True)
    async def softban_member(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        """ logic of softban is remove all roles and give they role name Handcuffed """
        """ Remember to save old roles to database for unsoftban """

        print("Softban command invoked")

        # check if member is bannable
        if member == ctx.author:
            await ctx.send("Bạn không thể tự nhốt mình vào ngục.")
            return
        
        if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send("Bạn không thể nhốt vào ngục người có vai trò cao hơn hoặc bằng bạn.")
            return
        
        # Add "Handcuffed" role
        handcuffed_role = discord.utils.get(ctx.guild.roles, name="Handcuffed")

        if not handcuffed_role:
            # throw that role not found error
            await ctx.send("Role 'Handcuffed' không tồn tại. Vui lòng tạo role này trước khi sử dụng lệnh softban.")
            return
        
        # check if member already has handcuffed role
        if handcuffed_role in member.roles:
            await ctx.send(f"{member.mention} đã bị nhốt vào ngục. Không thể nhốt lại.")
            return

        # Save old roles to database
        old_roles = [role.id for role in member.roles if role.name != "@everyone"]
        self.save_old_roles(member.id, old_roles)

        # Remove all roles
        await member.edit(roles=[])

        await member.add_roles(handcuffed_role)
        await ctx.send(f"Đã nhốt {member.mention} vào ngục. Lý do: {reason}")

    @commands.command(name="unsoftban")
    @commands.has_permissions(ban_members=True)
    async def unsoftban_member(self, ctx: commands.Context, member: discord.Member):
        """ Restore old roles from database and remove Handcuffed role """

        # Retrieve old roles from database
        old_roles_ids = self.get_old_roles(member.id)
        if not old_roles_ids:
            await ctx.send(f"Không tìm thấy vai trò cũ cho {member.mention}.")
            return

        # Remove "Handcuffed" role
        handcuffed_role = discord.utils.get(ctx.guild.roles, name="Handcuffed")
        if handcuffed_role in member.roles:
            await member.remove_roles(handcuffed_role)

        # Restore old roles
        old_roles = [discord.utils.get(ctx.guild.roles, id=role_id) for role_id in old_roles_ids]
        await member.add_roles(*[role for role in old_roles if role is not None])
        await ctx.send(f"Đã thả {member.mention} khỏi ngục và khôi phục vai trò cũ.")

async def setup(bot: commands.Bot):
    await bot.add_cog(SoftbanCog(bot))