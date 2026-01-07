import discord
from discord.ext import commands
import random
import datetime
import asyncio

class ShipMeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.FAKE_LOADING_SENTENCES = bot.FAKE_LOADING_SENTENCES

    @commands.command(name="ship", help="Đo mức độ hợp đôi của hai người dùng.")
    async def ship_meter(self, ctx, member1: discord.Member = None, member2: discord.Member = None):
        if member1 is None or member2 is None:
            await ctx.reply("Vui lòng tag hai thành viên để thực hiện đo mức độ hợp đôi.")
            return
        
        # Simulate a loading process with fake sentences
        loading_message = await ctx.send("Đang lấy dữ liệu.. ⏳")
        await asyncio.sleep(1)  # Initial wait time
        
        # get random 3 sentences to simulate loading
        random_sentences = random.sample(self.FAKE_LOADING_SENTENCES, min(3, len(self.FAKE_LOADING_SENTENCES)))
        
        for sentence in random_sentences:
            await loading_message.edit(content=f"{sentence} ⏳")
            await asyncio.sleep(3)  # Pause for a second to simulate loading

        # make the seed based on user ids to ensure consistent results (plus the day to vary daily)
        random.seed(f"{member1.id}-{member2.id}-{datetime.date.today()}")

        ship_percentage = random.randint(0, 100)
        
        # Create progress bar
        bar_length = 20
        filled = int((ship_percentage / 100) * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)

        # make the message blank first
        await loading_message.edit(content="Hoàn thành đo! 🎉")

        # Create embed
        embed = discord.Embed(
            title="❤️ Ship Meter ❤️",
            color=discord.Color.purple()
        )
        embed.add_field(
            name="👤 Người ấy",
            value=member1.mention,
            inline=True
        )

        embed.add_field(
            name="👤 Người kia",
            value=member2.mention,
            inline=True
        )

        embed.add_field(
            name="Độ hợp đôi:",
            value=f"{bar} **{ship_percentage}%**",
            inline=False
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        
        if ship_percentage < 30:
            image_url = "https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExcHRhOXkzOXFuaWIwajVybGJtenNkZ2Jna3k4MW1qNmxscWdobWp3biZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/9xijGdDIMovchalhxN/giphy.gif"
        elif ship_percentage < 60:
            image_url = "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExbHViNGV4dTg0YjE4c2I2ajVscTJuOGVjOTkxdmwzcjFjM3k1ZGl2dyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/kFIfiwvzJjbUsNbIg5/giphy.gif"
        elif ship_percentage < 85:
            image_url = "https://media4.giphy.com/media/v1.Y2lkPTc5MGI3NjExZXNleHd3eDl2czhtNGx5dmcwZmFyanBrN3lxaDJpa2w4c3diN3N0OCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/iyN7EivGQSUxi/giphy.gif"
        else:
            image_url = "https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExbWh5NnI0MHcybDZ6ZHh0eHE5bDJwNGdxM2wzMG5idDJmNGJkbmNqNCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/8H2kK5YIxvmHC/giphy.gif"

        embed.set_image(url=image_url)

        embed.set_footer(text=f"{member1} và {member2} hợp đôi {ship_percentage}%!\n```Chịu thôi, định mệnh mà!```")
        embed.set_footer(
            text="Kết quả này là thật, phải gì ạ? Phải chịuuuuuu! 🥰"
        )
        await loading_message.edit(embed=embed)

async def setup(bot):
    await bot.add_cog(ShipMeterCog(bot))