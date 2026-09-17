import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class GyattCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="gyatt", help="Đo lường mức độ gyatt của một người dùng.")
    async def gyatt_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ gyatt... ⏳🍑",
            done_message="Hoàn thành đo độ gyatt! 🎉",
            emoji="🍑",
        )

        percentage = get_daily_percentage(member.id, "gyatt")
        tease = pick_tease(
            percentage,
            [
                (10, "Chủ tịch hội Trái Đất phẳng. Tôn trọng lore."),
                (30, "Bánh nhẹ. Tiệm bánh đóng cửa hầu hết các ngày."),
                (50, "Hiện diện certified. Đầu quay tốc độ vừa."),
                (70, "Tiệm bánh mở. Kẹt xe ở hành lang."),
                (90, "CẢNH BÁO GYATT. Phát hiện địa chấn."),
                (None, "GYATT HUYỀN THOẠI. NASA muốn footage vệ tinh."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🍑 Gyatt Meter",
            description=f"Mức độ gyatt của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Khoa học chưa sẵn sàng cho con số này. 🍑",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(GyattCog(bot))
