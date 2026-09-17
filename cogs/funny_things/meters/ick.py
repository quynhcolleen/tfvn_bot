import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class IckCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ick", help="Đo lường mức độ ick của một người dùng.")
    async def ick_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ ick... ⏳🤢",
            done_message="Hoàn thành đo độ ick! 🎉",
            emoji="🤢",
        )

        percentage = get_daily_percentage(member.id, "ick")
        tease = pick_tease(
            percentage,
            [
                (10, "Không ick. Main character vệ sinh."),
                (30, "Ick nhẹ. Thỉnh thoảng nhai to. Sống được."),
                (50, "Cảnh báo ick. Ai đó thấy vấn đề tất."),
                (70, "Ick nặng. Attraction out group chat."),
                (90, "Siêu tân tinh ick. Chó cũng liếc mày."),
                (None, "TẬN THẾ ICK. Khoa học sơ tán khỏi chat."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🤢 Ick Meter",
            description=f"Mức độ ick của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Ick là có thật và nó vote. 🤢",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(IckCog(bot))
