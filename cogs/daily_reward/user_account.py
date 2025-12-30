import discord
from discord.ext import commands

class UserAccountCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="user_balance")
    async def user_balance(self, ctx):
        user_id = ctx.author.id
        balance = self.db["user_accounts"].find_one({"user_id": user_id})

        if not balance:
            self.db["user_accounts"].insert_one({"user_id": user_id, "balance": 0})
            balance = {"balance": 0}
        
        balance = balance["balance"] if balance else 0
        
        await ctx.send(f"{ctx.author.mention}, đây là số dư tài khoản của bạn: {balance} 💰")

    @commands.command(name="user_transactions")
    async def user_transactions(self, ctx):
        # Implementation for user transactions command
        pass

async def setup(bot: commands.Bot):
    await bot.add_cog(UserAccountCog(bot))