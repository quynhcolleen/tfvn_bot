import discord 
from discord.ext import commands
import datetime
import asyncio

class HappyNewYear2026CommandCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.start_time = datetime.datetime(2025, 12, 31, 17, 0, 0)
        self.end_time = datetime.datetime(2026, 1, 1, 17, 0, 0)
    
    @commands.Cog.listener()
    async def on_message(self, ctx: discord.Message):
        if ctx.author == self.bot.user:
            return
        
        # needing it have keyword "năm mới", "nmvv", 2026, happy new year in message
        if not any(keyword in ctx.content.lower() for keyword in ["năm mới", "nmvv", "2026", "new year"]):
            print("No New Year 2026 keywords found.")
            return
        
        # this bot will work from 2024-12-31 17:00 UTC to 2025-01-01 17:00 UTC
        now = datetime.datetime.utcnow()
        
        if not (self.start_time <= now <= self.end_time):
            print("Not in New Year 2026 time window.")
            return
        
        # check if that user have received the greeting already
        record = self.db["happy_new_year_2026"].find_one({"user_id": ctx.author.id})
        if record:
            return
        
        # send greeting message
        await ctx.reply(
            f"Chúc mừng năm mới 2026 {ctx.author.mention}! 🎉🎊\n"
            f"Chúc anh/chị/em năm mới tràn đầy niềm vui, sức khỏe và thành công, sẽ xinh đẹp hơn, giỏi giang hơn và hạnh phúc hơn! 🥳",
        )

        # record that the greeting has been sent
        self.db["happy_new_year_2026"].insert_one({"user_id": ctx.author.id, "timestamp": now})
        
async def setup(bot: commands.Bot):
    await bot.add_cog(HappyNewYear2026CommandCog(bot))        
