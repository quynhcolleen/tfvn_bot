import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class NpcCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="npc", help="Đo lường mức độ NPC của một người dùng.")
    async def npc_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang kiểm tra độ NPC... ⏳🧍",
            done_message="Hoàn thành đo độ NPC! 🎉",
            emoji="🧍",
        )

        percentage = get_daily_percentage(member.id, "npc")
        tease = pick_tease(
            percentage,
            [
                (10, "Player control full. Cây thoại hoang dã."),
                (30, "Routine nhẹ. Vẫn có side quest."),
                (50, "Lặp ba câu thoại mỗi ngày."),
                (70, "Đứng im. Chờ player tới."),
                (90, "Dấu chấm than trên đầu chỉ là glitch."),
                (None, "NPC MAX. 'Chào lữ khách' là cả tính cách."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="🧍 NPC Meter",
            description=f"Mức độ NPC của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Bấm E để tương tác. Hoặc đừng. 🧍",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(NpcCog(bot))
