import discord
import datetime
from discord.ext import commands

class PruneCommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        
    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def prune_messages(self, ctx: commands.Context, number: int):
        
        # purge number of messages 
        deleted = await ctx.channel.purge(limit=number + 1)  # +1 to include the command message
        
        await ctx.send(f"Đã xóa {len(deleted)-1} tin nhắn.", ephemeral=True, delete_after=5)

    @commands.command(name="purge_user")
    @commands.has_permissions(manage_messages=True)
    async def prune_user_messages(self, ctx: commands.Context, user: discord.Member, number: int):
        def is_user(m):
            return m.author == user
        
        deleted = await ctx.channel.purge(limit=number + 1, check=is_user)
        await ctx.send(f"Đã xóa {len(deleted)-1} tin nhắn của {user.mention}.", ephemeral=True, delete_after=5)

async def setup(bot: commands.Bot):
    await bot.add_cog(PruneCommandCog(bot))