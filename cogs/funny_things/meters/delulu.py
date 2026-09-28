import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class DeluluCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="delulu", help="Đo lường mức độ delulu của một người dùng.")
    async def delulu_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ delulu... ⏳🌀",
            done_message="Hoàn thành đo độ delulu! 🎉",
            emoji="🌀",
        )

        percentage = get_daily_percentage(member.id, "delulu")
        tease = pick_tease(
            percentage,
            [
                (10, "Tỉnh táo đau đớn. Hít cỏ mỗi ngày."),
                (30, "Mơ mộng nhẹ. Edit main character vô hại."),
                (50, "Họ sẽ nhắn lại. Sẽ. Ngày nào đó."),
                (70, "Viết lời thề hôn nhân cho cái situationship."),
                (90, "Đặt tên con rồi. Crush nói 'lol' đúng một lần."),
                (None, "DELULU FULL. Thực tại out chat rồi."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🌀 Delulu Meter",
            description=f"Mức độ delulu của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Delulu is the solulu... cho đến khi không còn. 🌀",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(DeluluCog(bot))
