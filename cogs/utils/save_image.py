import discord
from discord.ext import commands
import asyncio
from datetime import datetime

class SaveImageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        
    async def log_image_save(self, ctx: discord.Message, image_collection_name: str, *metadata_args):
        saved_image_id = []
        # Parse metadata arguments as key-value pairs
        metadata_dict = {}

        for i in range(0, len(metadata_args), 2):
            if i + 1 < len(metadata_args):
                key = metadata_args[i]
                value = metadata_args[i + 1]
                metadata_dict[key] = value
                print(f"Added metadata key-value pair: {key} = {value}")
        
        print(f"Metadata dict: {metadata_dict}")
        
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
                "metadata": metadata_dict,
                "saved_at": datetime.utcnow()
            }
            
            saved_image = self.db['images'].insert_one(doc)
            saved_image_id.append(str(saved_image.inserted_id))

        return saved_image_id if saved_image_id else None
            
    
    @commands.command(name='save_image', help='Saves attached images to the database.')
    @commands.has_permissions(manage_messages=True)
    async def save_image_cmd(self, ctx, collection_name: str, *metadata_args):
        if not ctx.message.attachments:
            return
        
        saved_image_ids = await self.log_image_save(ctx, collection_name, *metadata_args)
        print(f"Saved image IDs: {saved_image_ids}")

        if (saved_image_ids):
            await ctx.send(f"Hình ảnh đã được lưu")
        else:
            await ctx.send("Có lỗi xảy ra khi lưu hình ảnh.")

        

async def setup(bot: commands.Bot):
    await bot.add_cog(SaveImageCog(bot))
