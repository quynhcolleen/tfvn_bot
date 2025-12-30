import discord
from discord.ext import commands
import datetime

class DailyActionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="daily")
    async def daily_reward(self, ctx):
        user_id = ctx.author.id
        today_start = datetime.datetime.combine(discord.utils.utcnow().date(), datetime.time.min)
        user_data = self.db['daily_rewards_logs'].find_one({
            "user_id": user_id,
            "date": {"$gte": today_start}
        })

        if user_data:
            await ctx.send(f"{ctx.author.mention}, bạn đã nhận phần thưởng hàng ngày hôm nay rồi! Vui lòng quay lại vào ngày mai.")
            return
        
        # Grant daily reward (for example, 10 trap coins)
        accounts = self.db["user_accounts"].find_one({"user_id": user_id})
        if not accounts:
            self.db["user_accounts"].insert_one({"user_id": user_id, "balance": 0})

        self.db["daily_rewards_logs"].insert_one({
            "user_id": user_id,
            "date": today_start
        })

        self.db["user_accounts"].update_one(
            {"user_id": user_id},
            {"$inc": {"balance": 10}}
        )

        await ctx.send(f"{ctx.author.mention}, bạn đã nhận phần thưởng hàng ngày của mình! 🎉")



async def setup(bot: commands.Bot):
    await bot.add_cog(DailyActionCog(bot))