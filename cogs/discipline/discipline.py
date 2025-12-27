import discord
from discord.ext import commands
import asyncio

def log_discipline_breach(message: discord.Message):
    """
    Logs a discipline breach message to the console.
    """
    print(f"[DISCIPLINE LOG] {message.author} (ID: {message.author.id}) in {message.channel} said: '{message.content}'")

class DisciplineCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.BANNED_WORDS = bot.BANNED_WORDS

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
        print(f"New message in {ctx.channel}: {ctx.author} (ID: {ctx.author.id}) said: '{ctx.content}'")
        # Example: If a specific word is in the message, the bot can respond.
        if any(bad_word in ctx.content.lower() for bad_word in self.BANNED_WORDS):
            log_discipline_breach(ctx)
            
            await ctx.add_reaction("⚠️")
            warn_msg = await ctx.reply(f"{ctx.author.mention}, chú ý cái mồm!")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.delete()
            
        # IMPORTANT: If you are using commands.Bot (as shown above),
        # you must include this line to allow commands (e.g., !mycommand) to work.
        await self.bot.process_commands(ctx)
    # Run the bot with your token
    # bot.run("YOUR_BOT_TOKEN")

async def setup(bot: commands.Bot):
    await bot.add_cog(DisciplineCog(bot))