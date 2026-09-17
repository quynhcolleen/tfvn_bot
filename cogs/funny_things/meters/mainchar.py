import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class MainCharCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="mainchar", help="Đo lường main character energy của một người dùng.")
    async def mainchar_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra main character energy... ⏳✨",
            done_message="Hoàn thành đo main character! 🎉",
            emoji="✨",
        )

        percentage = get_daily_percentage(member.id, "mainchar")
        tease = pick_tease(
            percentage,
            [
                (10, "Extra nền. Một tập chớp mắt một lần."),
                (30, "Side character có tên. Một câu hài."),
                (50, "Cast phụ có arc. Screen time đáng nể."),
                (70, "Năng lượng co-lead. Nhạc nổi khi mày vào."),
                (90, "Main character confirmed. Mưa theo mood mày."),
                (None, "NHÂN VẬT CHÍNH THỰC TẠI. Credit lăn cho người khác."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="✨ Main Character Meter",
            description=f"Main character energy của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Plot armor có cũng được không cũng được. ✨",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(MainCharCog(bot))
