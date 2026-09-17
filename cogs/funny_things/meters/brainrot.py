import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class BrainrotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="brainrot", help="Đo lường mức độ brainrot của một người dùng.")
    async def brainrot_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ brainrot... ⏳🧠",
            done_message="Hoàn thành đo độ brainrot! 🎉",
            emoji="🧠",
        )

        percentage = get_daily_percentage(member.id, "brainrot")
        tease = pick_tease(
            percentage,
            [
                (10, "Arc học giả. Đọc sách không cần caption."),
                (30, "Dư meme nhẹ. Vẫn nói câu hoàn chỉnh."),
                (50, "Nói toàn reference. Bạn bè phiên dịch."),
                (70, "Tần số skibidi. Tập trung: 3 giây."),
                (90, "Não chỉ còn cache TikTok. Reboot giúp."),
                (None, "BRAINROT GIAI ĐOẠN CUỐI. Chỉ nói tiếng Ohio."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🧠 Brainrot Meter",
            description=f"Mức độ brainrot của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Não mày đang buffering. 🧠",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(BrainrotCog(bot))
