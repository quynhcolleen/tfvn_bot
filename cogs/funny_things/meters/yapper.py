import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class YapperCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="yapper", help="Đo lường mức độ yap của một người dùng.")
    async def yapper_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ yap... ⏳🗣️",
            done_message="Hoàn thành đo độ yap! 🎉",
            emoji="🗣️",
        )

        percentage = get_daily_percentage(member.id, "yapper")
        tease = pick_tease(
            percentage,
            [
                (10, "Ít nói. Gửi một emoji rồi offline."),
                (30, "Tán gẫu nhẹ. Để bong bóng typing dở."),
                (50, "Yap ổn định. Group chat sống nhờ mày."),
                (70, "Yapper chuyên nghiệp. Essay trong replies."),
                (90, "Voice note dài hơn podcast. Không note."),
                (None, "THẦN YAP. Từ/phút: phạm luật."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🗣️ Yapper Meter",
            description=f"Mức độ yap của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Nói nhiều. Nói hết. Và chẳng nói gì. 🗣️",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(YapperCog(bot))
