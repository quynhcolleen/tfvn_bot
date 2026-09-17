import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class CringeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="cringe", help="Đo lường mức độ cringe của một người dùng.")
    async def cringe_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ cringe... ⏳🫣",
            done_message="Hoàn thành đo độ cringe! 🎉",
            emoji="🫣",
        )

        percentage = get_daily_percentage(member.id, "cringe")
        tease = pick_tease(
            percentage,
            [
                (10, "Aura chill hết nấc. Nể."),
                (30, "Lore nhẹ. Cái joke 2019 vẫn ám mày."),
                (50, "Xấu hổ hộ detectable. Bạn bè giật mình."),
                (70, "Radar cringe hú. Xóa post đó. Ngay."),
                (90, "Xấu hổ cấp quốc gia. Group chat có đền thờ."),
                (None, "CRINGE TỐI ĐA. Khảo cổ đào được tweet này."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🫣 Cringe Meter",
            description=f"Mức độ cringe của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Xấu hổ hộ miễn phí. Không cần cảm ơn. 🫣",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(CringeCog(bot))
