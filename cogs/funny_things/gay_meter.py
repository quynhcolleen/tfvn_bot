import discord
from discord.ext import commands
import random
import datetime
import asyncio

class GayMeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.FAKE_LOADING_SENTENCES = bot.FAKE_LOADING_SENTENCES

    @commands.command(name="gaymeter", help="Đo lường mức độ gay của một người dùng.")
    async def gay_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        # Simulate a loading process with fake sentences
        loading_message = await ctx.send("Đang đo lường... ⏳")
        
        # get random 3 sentences to simulate loading
        random_sentences = random.sample(self.FAKE_LOADING_SENTENCES, min(3, len(self.FAKE_LOADING_SENTENCES)))
        
        for sentence in random_sentences:
            await loading_message.edit(content=f"{sentence} ⏳")
            await asyncio.sleep(3)  # Pause for a second to simulate loading

        # make the seed based on user id to ensure consistent results (plus the day to vary daily)
        random.seed(f"{member.id}-{datetime.date.today()}")

        gay_percentage = random.randint(0, 100)
        
        # Create progress bar
        bar_length = 30
        filled = int((gay_percentage / 100) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)

        # make the message blank first
        await loading_message.edit(content="Hoàn thành đo lường! 🎉")

        # Create embed
        embed = discord.Embed(
            title="🏳️‍🌈 Gay Meter 🏳️‍🌈",
            description=f"Mức độ gay của {member.mention}",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.add_field(
            name="Kết quả",
            value=f"{bar} **{gay_percentage}%**",
            inline=False
        )
        embed.set_footer(text=f"Người dùng {member} gay {gay_percentage}%!")

        await loading_message.edit(embed=embed)

async def setup(bot):
    await bot.add_cog(GayMeterCog(bot))