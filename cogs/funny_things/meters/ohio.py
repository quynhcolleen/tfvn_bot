import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class OhioCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ohio", help="Đo lường mức độ Ohio của một người dùng.")
    async def ohio_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ Ohio... ⏳🌽",
            done_message="Hoàn thành đo độ Ohio! 🎉",
            emoji="🌽",
        )

        percentage = get_daily_percentage(member.id, "ohio")
        tease = pick_tease(
            percentage,
            [
                (10, "Bình thường. Không phải bang đó. Bang khác."),
                (30, "Năng lượng bắp nhẹ. Thứ Ba hơi cursed."),
                (50, "Có gì đó sai. Gấu mèo đang tổ chức."),
                (70, "Sự kiện chỉ ở Ohio đang load... chờ."),
                (90, "Thực tại glitch. GPS hiện '???'."),
                (None, "OHIO FULL. Bắp đã lập công đoàn chống mày."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🌽 Ohio Meter",
            description=f"Mức độ Ohio của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Chỉ ở Ohio. Luôn ở Ohio. 🌽",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(OhioCog(bot))
