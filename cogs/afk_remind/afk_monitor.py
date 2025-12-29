import discord
from discord.ext import commands


class MonitorAfkMessageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not message.mentions:
            return

        mentioned_user_ids = [user.id for user in message.mentions]
        current_time = discord.utils.utcnow()

        afk_reminders = self.db["afk_reminders"].find(
            {"user_id": {"$in": mentioned_user_ids}, "end_at": {"$gt": current_time}}
        )

        for reminder in afk_reminders:
            user = message.guild.get_member(reminder["user_id"])
            if not user:
                continue

            remind_message = reminder["message"]

            await message.channel.send(
                f"{message.author.mention}, "
                f"{user.display_name} đang AFK:\n> {remind_message}"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorAfkMessageCog(bot))
