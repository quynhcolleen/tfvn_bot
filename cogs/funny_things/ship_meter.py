import discord
from discord.ext import commands
import random
import datetime
import asyncio

class ShipMeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.FAKE_LOADING_SENTENCES = bot.FAKE_LOADING_SENTENCES

    @commands.command(name="shipmeter", help="Đo lường mức độ hợp đôi của hai người dùng.")
    async def ship_meter(self, ctx, member1: discord.Member = None, member2: discord.Member = None):
        if member1 is None or member2 is None:
            await ctx.send("Vui lòng đề cập hai thành viên để đo lường mức độ hợp đôi.")
            return

        # Simulate a loading process with fake sentences
        loading_message = await ctx.send("Đang đo lường... ⏳")
        
        # get random 3 sentences to simulate loading
        random_sentences = random.sample(self.FAKE_LOADING_SENTENCES, min(3, len(self.FAKE_LOADING_SENTENCES)))
        
        for sentence in random_sentences:
            await loading_message.edit(content=f"{sentence} ⏳")
            await asyncio.sleep(3)  # Pause for a second to simulate loading

        # make the seed based on user ids to ensure consistent results (plus the day to vary daily)
        random.seed(f"{member1.id}-{member2.id}-{datetime.date.today()}")

        ship_percentage = random.randint(0, 100)
        
        # Create progress bar
        bar_length = 30
        filled = int((ship_percentage / 100) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)

        # make the message blank first
        await loading_message.edit(content="Hoàn thành đo lường! 🎉")

        # Create embed
        embed = discord.Embed(
            title="❤️ Ship Meter ❤️",
            description=f"Mức độ hợp đôi của {member1.mention} và {member2.mention}",
            color=discord.Color.purple()
        )
        embed.add_field(
            name="Kết quả",
            value=f"{bar} **{ship_percentage}%**",
            inline=False
        )
        embed.set_footer(text=f"{member1} và {member2} hợp đôi {ship_percentage}%!")

        await loading_message.edit(embed=embed)

async def setup(bot):
    await bot.add_cog(ShipMeterCog(bot))