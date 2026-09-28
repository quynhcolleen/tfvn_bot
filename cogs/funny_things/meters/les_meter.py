import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class LesMeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="les", aliases=["les_meter", "lesbian"], help="Đo lường mức độ les của một người dùng.")
    async def les_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ les... ⏳🧡",
            done_message="Hoàn thành đo độ les! 🎉",
            emoji="🧡",
        )

        percentage = get_daily_percentage(member.id, "les")
        tease = pick_tease(
            percentage,
            [
                (10, "Thẳng thế này thì chịu luôn!"),
                (30, "Gồng ác ghê mày?"),
                (50, "Ê hơi les rồi đó nha!"),
                (70, "Les lộ bà ơi!"),
                (90, "Les vãi chưởng!"),
                (None, "Les quáaaaa quỷ sứ hà ahihi!"),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🧡 Les Meter 🧡",
            description=f"Mức độ les của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Kết quả này là thật, phải gì ạ? Phải chịuuuuuu! 🧡💜",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(LesMeterCog(bot))
