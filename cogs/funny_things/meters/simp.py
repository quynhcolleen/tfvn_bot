import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class SimpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="simp", help="Đo lường mức độ simp của một người dùng.")
    async def simp_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ simp... ⏳😭",
            done_message="Hoàn thành đo độ simp! 🎉",
            emoji="😭",
        )

        percentage = get_daily_percentage(member.id, "simp")
        tease = pick_tease(
            percentage,
            [
                (10, "Tim thép. Story người ta cũng không bấm like."),
                (30, "Like cho có. Vẫn còn chút danh dự... tạm thời."),
                (50, "Trả tiền cafe. Nhớ luôn order của crush."),
                (70, "Ví khóc thét. Quà sinh nhật đặt từ tháng 3."),
                (90, "Thú cưng hỗ trợ cảm xúc full-time không lương."),
                (None, "Đồng vị SIMPoni. Giới khoa học đang lo."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="😭 Simp Meter",
            description=f"Mức độ simp của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Kết quả này là thật, phải gì ạ? Phải chịuuuuuu! 😭",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(SimpCog(bot))
