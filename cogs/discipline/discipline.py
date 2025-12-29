import discord
from discord.ext import commands
import asyncio
from datetime import datetime

class DisciplineCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.BANNED_WORDS = bot.BANNED_WORDS

    async def log_discipline_breach(self, message: discord.Message):
        """
        Logs a discipline breach message to the console.
        """
        log_data = {
            "author": str(message.author),
            "author_id": message.author.id,
            "channel": str(message.channel),
            "channel_id": message.channel.id,
            "content": message.content,
            "timestamp": datetime.utcnow()  # UTC timestamp
        }
        
        # Insert into the 'discipline_logs' collection
        collection = self.db['discipline_logs']
        collection.insert_one(log_data)
        
        # Optional: Still print to console for debugging
        # print(f"[DISCIPLINE LOG] {message.author} (ID: {message.author.id}) in {message.channel} said: '{message.content}'")

    @commands.Cog.listener()
    async def on_message(self, ctx: discord.Message):
        """
        This event fires whenever a message is sent in a channel the bot can see.
        """
        # CRITICAL: Prevent the bot from responding to its own messages,
        # which can cause infinite loops.
        if ctx.author == self.bot.user:
            return

        # You can now "watch" the message content, author, channel, etc.
        # print(f"New message in {ctx.channel}: {ctx.author} (ID: {ctx.author.id}) said: '{ctx.content}'")
        # Example: If a specific word is in the message, the bot can respond.
        if any(bad_word in ctx.content.lower() for bad_word in self.BANNED_WORDS):
            await self.log_discipline_breach(ctx)
            
            await ctx.add_reaction("⚠️")
            warn_msg = await ctx.reply(f"{ctx.author.mention}, chú ý cái mồm! Lần vi phạm thứ {self.db['discipline_logs'].count_documents({'author_id': ctx.author.id})} rồi đó!")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.delete()

        # IMPORTANT: If you are using commands.Bot (as shown above),
        # await self.bot.process_commands(ctx)

async def setup(bot: commands.Bot):
    await bot.add_cog(DisciplineCog(bot))