import discord
from discord.ext import commands
import random

class RandomFemboyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name='random_femboy', help='Selects a random femboy from the server.')
    async def random_femboy(self, ctx):
        """ Select a random image of femboy from images collection with image_collection is femboy """
        collection = self.db['images']
        fembi_num = collection.count_documents({"image_collection": "femboy"})
        if fembi_num == 0:
            await ctx.send("Hiện không có hình femboy nào được lưu trong cơ sở dữ liệu.")
            return
        
        random_number = random.randint(0, fembi_num - 1)
        femboy_images_collection = list(collection.find({"image_collection": "femboy"}).skip(random_number).limit(1))
        random_image_collection = femboy_images_collection[0]

        await ctx.send(random_image_collection['url'])

        # trying to get social media from metadata
        metadata = random_image_collection.get('metadata', {})
        
        embed = discord.Embed(title="Thông tin thêm về femboy này")
        if 'fb' in metadata:
            embed.add_field(name="📘 Facebook", value=metadata['fb'], inline=False)
        if 'ig' in metadata:
            embed.add_field(name="📸 Instagram", value=metadata['ig'], inline=False)
        if 'x' in metadata:
            embed.add_field(name="❌ X", value=metadata['x'], inline=False)

        # embed color set to femboy pink
        embed.color = 0xFF69B4
            
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(RandomFemboyCog(bot))