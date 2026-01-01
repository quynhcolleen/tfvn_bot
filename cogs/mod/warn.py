import discord
from discord.ext import commands
import random
import asyncio

class WarnCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="warn")
    @commands.has_permissions(manage_messages=True)
    async def warn_user(self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason provided"):
        if user == ctx.author:
            await ctx.send("Bạn không thể tự cảnh cáo chính mình.", delete_after=10)
            return
        
        warn_data = {
            "user_id": user.id,
            "user_name": str(user),
            "moderator_id": ctx.author.id,
            "moderator_name": str(ctx.author),
            "reason": reason,
            "timestamp": discord.utils.utcnow()
        }
        
        # Insert into the 'warnings' collection
        collection = self.db['warnings']
        collection.insert_one(warn_data)
        
        embed = discord.Embed(title="Cảnh cáo người dùng", color=discord.Color.orange())
        embed.add_field(name="Thành viên", value=f"{user.mention} (ID: {user.id})", inline=False)
        embed.add_field(name="Mod", value=f"{ctx.author.mention} (ID: {ctx.author.id})", inline=False)
        embed.add_field(name="Lý do", value=reason, inline=False)

        await ctx.send(embed=embed, delete_after=60)
    @warn_user.error
    async def warn_user_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn không có quyền để sử dụng lệnh này.", delete_after=10)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Vui lòng cung cấp thành viên cần cảnh cáo.", delete_after=10)
        else:
            await ctx.send("Đã xảy ra lỗi khi xử lý lệnh cảnh cáo.", delete_after=10)

    @commands.command(name="check_warn")
    async def check_warnings(self, ctx: commands.Context, user: discord.Member = None):
        if user is None:
            user = ctx.author
            
        collection = self.db['warnings']
        warnings = list(collection.find({"user_id": user.id}))
        
        embed = discord.Embed(title="Lịch sử cảnh cáo", color=discord.Color.blue())
        
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        if not warnings:
            embed.add_field(name="Không có cảnh cáo", value="Người dùng này chưa bị cảnh cáo.", inline=False)
        else:
            for warn in warnings:
                timestamp = warn.get("timestamp", "Không xác định").strftime("%Y-%m-%d %H:%M:%S")
                reason = warn.get("reason", "Không có lý do cụ thể")
                moderator_name = warn.get("moderator_name", "Không xác định")
                embed.add_field(
                    name=f"Cảnh cáo vào {timestamp}",
                    value=f"Lý do: {reason}\nMod: {moderator_name}",
                    inline=False
                )

        await ctx.send(embed=embed)
                

async def setup(bot: commands.Bot):
    await bot.add_cog(WarnCommandCog(bot))