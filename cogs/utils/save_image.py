import discord
from discord.ext import commands
import asyncio
from datetime import datetime

class SaveImageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        
    async def log_image_save(self, ctx: discord.Message, image_collection_name: str):
        for att in ctx.message.attachments:
            if not att.content_type or not att.content_type.startswith('image/'):
                continue
                
            doc = {
                "guild_id": ctx.guild.id,
                "channel_id": ctx.channel.id,
                "message_id": ctx.message.id,
                "attachment_id": att.id,
                "filename": att.filename,
                "image_collection": image_collection_name,
                "url": att.url,                    # permanent
                "proxy_url": att.proxy_url,
                "saved_at": datetime.utcnow()
            }
            
            saved_image = self.db['images'].insert_one(doc)
        
            return saved_image.inserted_id
    
    @commands.command(name='save_image', help='Saves attached images to the database.')
    async def save_image_cmd(self, ctx, collection_name: str):
        if not ctx.message.attachments:
            return
        
        saved_image_id = await self.log_image_save(ctx, collection_name)

        if (saved_image_id):
            await ctx.send(f"Hình ảnh đã được lưu")
        else:
            await ctx.send("Có lỗi xảy ra khi lưu hình ảnh.")

        

async def setup(bot: commands.Bot):
    await bot.add_cog(SaveImageCog(bot))
        
        