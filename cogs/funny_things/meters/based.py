import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class BasedCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="based", help="Đo lường mức độ based của một người dùng.")
    async def based_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ based... ⏳🗿",
            done_message="Hoàn thành đo độ based! 🎉",
            emoji="🗿",
        )

        percentage = get_daily_percentage(member.id, "based")
        tease = pick_tease(
            percentage,
            [
                (10, "Cringe terminal. Đọc triết + hít cỏ đi."),
                (30, "Có opinion nhẹ. Vẫn check Twitter trước."),
                (50, "Take cân bằng. Hỗn loạn và trật tự hòa hợp."),
                (70, "Phòng based đang gọi. Nhấc máy đi."),
                (90, "Vào phòng là Overton window dịch chuyển."),
                (None, "MOAI SỐNG. Không lay. Không care. Thất nghiệp? Có thể."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🗿 Based Meter",
            description=f"Mức độ based của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Hòn đá đã phán. Phải chịuuuuuu! 🗿",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(BasedCog(bot))
