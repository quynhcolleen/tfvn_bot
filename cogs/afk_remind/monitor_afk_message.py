import discord
from discord.ext import commands
import aiohttp

class MonitorAfkMessageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.Cog.listener()
    async def on_message(self, ctx: discord.Message):
        """Monitors messages and sends AFK reminders if necessary."""
        if ctx.author.bot:
            return

        """ check if the message mentions any user who has set an AFK reminder """
        print(f"New message in {ctx.channel}: {ctx.author} (ID: {ctx.author.id}) said: '{ctx.content}'")

        mentioned_user_ids = [user.id for user in ctx.mentions]
        if not mentioned_user_ids:
            return
        
        current_time = discord.utils.utcnow()
        afk_reminders = self.db['afk_reminders'].find({
            "user_id": {"$in": mentioned_user_ids},
            "end_at": {"$gt": current_time}
        })
        for reminder in afk_reminders:
            user = ctx.guild.get_member(reminder['user_id'])
            if user:
                remind_message = reminder['remind_message']
                await ctx.reply(f"{ctx.author.mention}, {user.display_name} đang AFK và để lại tin nhắn: {remind_message}")

        
        
async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorAfkMessageCog(bot))
