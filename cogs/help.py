import asyncio
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def custom_help(self, ctx):
        embed = discord.Embed(
            title="📖 Hướng dẫn sử dụng TFVN bot",
            description="Danh sách lệnh hiện có:",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="General",
            value=(
                "`!tf hello` – Chào con bot.\n"
                "`!tf verify` – Hướng dẫn chứng thực độ tuổi.\n"
                "`!tf cat` – Mèo.\n"
                "`!tf dog` – Chó.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="NSFW",
            value=(
                "`!tf nsfw` – Hướng dẫn lệnh nsfw.\n"
                "`!tf verify` – Hướng dẫn chứng thực độ tuổi.\n"

            ),
            inline=False,
        )

        embed.set_footer(text="Prefix: !tf")

        await ctx.send(embed=embed)
        
    @commands.command(name="nsfw")
    async def nsfw_help(self, ctx):
        embed = discord.Embed(
            title="Lệnh NSFW",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="NSFW Commands",
            value=(
                "`!tf r34 <tags>` – Tìm kiếm ảnh/video trên Rule34.\n"
                "`!tf gbr <tags>` – Tìm kiếm ảnh/video trên Gelbooru.\n"
            ),
            inline=False,
        )
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.reply(embed=embed)
            
async def setup(bot):
    await bot.add_cog(HelpCog(bot))
