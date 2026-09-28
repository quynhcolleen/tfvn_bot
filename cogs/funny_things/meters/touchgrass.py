import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class TouchGrassCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="touchgrass", help="Đo mức độ cần ra ngoài hít cỏ của một người dùng.")
    async def touchgrass_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra vitamin nắng... ⏳🌿",
            done_message="Hoàn thành đo touch grass! 🎉",
            emoji="🌿",
        )

        percentage = get_daily_percentage(member.id, "touchgrass")
        tease = pick_tease(
            percentage,
            [
                (10, "Hôn bởi nắng. Chim biết tên mày."),
                (30, "Game thỉnh thoảng. Vẫn biết mây trông ra sao."),
                (50, "Vitamin D thấp. Rèm đóng vĩnh viễn."),
                (70, "Lore tái. Thời gian đo bằng patch và season pass."),
                (90, "Quang hợp bất khả. Gửi cứu trợ và một cái cửa sổ."),
                (None, "DỊ ỨNG CỎ + ONLINE MÃN TÍNH. Chạm khái niệm cỏ đi."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🌿 Touch Grass Meter",
            description=f"Mức độ cần hít cỏ của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Cỏ là có thật. Ra ngoài đi. 🌿",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(TouchGrassCog(bot))
