import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class ClownCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="clown", help="Đo lường mức độ hề của một người dùng.")
    async def clown_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ hề... ⏳🤡",
            done_message="Hoàn thành đo độ hề! 🎉",
            emoji="🤡",
        )

        percentage = get_daily_percentage(member.id, "clown")
        tease = pick_tease(
            percentage,
            [
                (10, "Chỉ việc nghiêm túc. Không makeup, không rạp."),
                (30, "Thỉnh thoảng tấu hài. Hề cung đình vô hại."),
                (50, "Giờ hề định kỳ. Bạn bè mang bắp."),
                (70, "Makeup full. Vẫn nhắn tin ex."),
                (90, "Xiếc lớn. Vé xem L của mày sold out."),
                (None, "CHỦ RẠP HỀ. Cái mũi gắn vĩnh viễn."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🤡 Clown Meter",
            description=f"Mức độ hề của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Honk honk. Rạp xiếc gọi. 🤡",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(ClownCog(bot))
