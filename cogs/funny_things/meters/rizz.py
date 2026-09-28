import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class RizzCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="rizz", help="Đo lường mức độ rizz của một người dùng.")
    async def rizz_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ rizz... ⏳💋",
            done_message="Hoàn thành đo độ rizz! 🎉",
            emoji="💋",
        )

        percentage = get_daily_percentage(member.id, "rizz")
        tease = pick_tease(
            percentage,
            [
                (10, "Rizz âm. Khai phá sản quyến rũ luôn đi."),
                (30, "Chỉ có dialogue NPC. Ra ngoài hít cỏ với soi gương đi."),
                (50, "Rizz nhẹ. Bà ngoại có thể đỏ mặt. Có thể."),
                (70, "Đang nấu chín vừa. Người ta cười joke của mày thiệt."),
                (90, "Steeze official. Vào phòng là không khí đổi tần."),
                (None, "RIZZ VÔ CỰC. Trọng lực với mày là optional."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="💋 Rizz Meter",
            description=f"Mức độ rizz của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Kết quả này là thật, phải gì ạ? Phải chịuuuuuu! 💋",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(RizzCog(bot))
