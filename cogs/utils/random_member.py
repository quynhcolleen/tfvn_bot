import discord
from discord.ext import commands
from typing import Union
import random

class RandomMemberCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='random_member', help='Selects a random member from the server.')
    async def random_member(self, ctx, target: Union[discord.Member, discord.Role] = None):
        """Selects and mentions a random member from the server."""
        if target is None:
            # No target specified: throw error that target is required
            await ctx.send("Vui lòng chỉ định một thành viên hoặc vai trò để chọn ngẫu nhiên.")
        elif isinstance(target, discord.Role):
            # Target is a role: select from members in that role
            members = [member for member in target.members if not member.bot]
        else:
            # Target is a member: select from that member only
            members = [target]
        
        if not members:
            await ctx.send("Không có thành viên nào để chọn.")
            return
        
        random_member = random.choice(members)
        await ctx.send(f"Chúc mừng: {random_member.mention} đã được lên ghế nóng! :Đ")

async def setup(bot: commands.Bot):
    await bot.add_cog(RandomMemberCog(bot))