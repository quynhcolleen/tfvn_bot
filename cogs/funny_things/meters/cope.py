import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class CopeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="cope", help="Đo lường mức độ cope / seethe / mald của một người dùng.")
    async def cope_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ cope / seethe / mald... ⏳🧠",
            done_message="Hoàn thành đo cope / seethe / mald! 🎉",
            emoji="🧠",
        )

        percentage = get_daily_percentage(member.id, "cope")
        tease = pick_tease(
            percentage,
            [
                (10, "Thiền tăng. Diff bật như đạn Nerf."),
                (30, "Cope nhẹ. Nhắc 'lag' đúng một lần."),
                (50, "Seethe thầm. Gõ phím dữ dội hơn."),
                (70, "Mald live. Bạn bè mute VC."),
                (90, "Triple: cope + seethe + mald. Huyền thoại."),
                (None, "MALD VŨ TRỤ. Server cảm nhận được rung."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🧠 Cope / Seethe / Mald Meter",
            description=f"Mức độ cope / seethe / mald của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Cope mạnh hơn. Seethe nhẹ hơn. Mald mãi mãi. 🧠",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(CopeCog(bot))
