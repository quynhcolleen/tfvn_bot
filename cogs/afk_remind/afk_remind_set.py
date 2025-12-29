from datetime import datetime, timedelta
import discord
from discord.ext import commands

class AfkRemindSetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
    
    @commands.command(name='set_afk_remind', help='Sets your AFK reminder message.')
    async def set_afk_remind(self, ctx, timeout_minutes: str = "5", *, remind_message: str):
        """Sets the AFK reminder message for the user."""
        # Manually convert timeout_minutes to int and validate
        try:
            minutes = int(timeout_minutes)
        except ValueError:
            await ctx.send("Thời gian timeout phải là một số nguyên dương (ví dụ: 5).")
            return
        
        if minutes <= 0:
            await ctx.send("Thời gian timeout phải lớn hơn 0 phút.")
            return
        
        user_id = ctx.author.id
        new_record = self.db['afk_reminders'].update_one(
            {"user_id": user_id, "end_at": {"$gt": datetime.utcnow()}},
            {"$set": {"remind_message": remind_message, "timeout_minutes": minutes, "end_at": datetime.utcnow() + timedelta(minutes=minutes)}},
            upsert=True
        )
        await ctx.send(f"Đã cài nhắc AFK với thông điệp: {remind_message}")
    
    @commands.command(name='clear_afk_remind', help='Clears your AFK reminder message.')
    async def clear_afk_remind(self, ctx):
        """Clears the AFK reminder message for the user."""
        user_id = ctx.author.id
        new_record = self.db['afk_reminders'].update_one(
            {"user_id": user_id, "end_at": {"$gt": datetime.utcnow()}},
            {"$set": {"end_at": datetime.utcnow()}},
            upsert=True
        )
        if new_record.modified_count == 0:
            await ctx.send("Bạn chưa cài nhắc AFK nào.")
            return
        
        await ctx.send("Đã xóa nhắc AFK của bạn.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AfkRemindSetCog(bot))