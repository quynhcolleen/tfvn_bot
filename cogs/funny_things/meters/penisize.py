import asyncio
import datetime
import random

import discord
from discord.ext import commands


class PeniSizeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.FAKE_LOADING_SENTENCES = bot.FAKE_LOADING_SENTENCES

    @commands.command(
        name="penisize",
        aliases=["peni", "peni_size", "ppsize"],
        help="Đo kích thước peni của một thành viên.",
    )
    async def peni_size(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await ctx.send("Đang lấy thước dây... ⏳")
        await asyncio.sleep(1)

        random_sentences = random.sample(
            self.FAKE_LOADING_SENTENCES, min(2, len(self.FAKE_LOADING_SENTENCES))
        )

        for sentence in random_sentences:
            await loading_message.edit(content=f"{sentence} ⏳")
            await asyncio.sleep(3)

        rng = random.Random(f"penisize-{member.id}-{datetime.date.today()}")
        size_cm = rng.randint(0, 30)
        size_mm = rng.randint(0, 9)
        display_size = f"{size_cm}.{size_mm} cm"
        ascii_size = max(1, round(size_cm / 2))
        peni_shape = f"8{'=' * ascii_size}D"

        if size_cm < 5:
            tease = "Kính hiển vi đang tăng ca."
        elif size_cm < 10:
            tease = "Nhỏ nhưng có võ... chắc vậy."
        elif size_cm < 16:
            tease = "Ổn áp, tiêu chuẩn phòng lab."
        elif size_cm < 23:
            tease = "Căng đấy, số liệu hơi đáng nể."
        else:
            tease = "Huyền thoại trong truyền thuyết."

        embed = discord.Embed(
            title="📏 Peni Size Meter 📏",
            description=f"Kích thước peni của {member.mention}",
            color=discord.Color.dark_gold(),
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="Kết quả:",
            value=f"```{peni_shape}```**{display_size}**\n```{tease}```",
            inline=False,
        )
        embed.set_footer(text="Kết quả này chỉ để giải trí thôi nha.")

        await loading_message.edit(content="Đo xong rồi đây! 🎉", embed=embed)


async def setup(bot):
    await bot.add_cog(PeniSizeCog(bot))
