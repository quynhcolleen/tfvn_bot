import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_percentage,
    build_meter_embed,
    pick_tease,
)


class SkillIssueCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="skillissue", help="Đo lường skill issue của một người dùng.")
    async def skillissue_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang chẩn đoán skill issue... ⏳💀",
            done_message="Hoàn thành chẩn đoán skill issue! 🎉",
            emoji="💀",
        )

        percentage = get_daily_percentage(member.id, "skillissue")
        tease = pick_tease(
            percentage,
            [
                (10, "Cracked thiệt. Diff mang tính cá nhân."),
                (30, "Lỗi nhẹ. Đổ lag tuần một lần."),
                (50, "Skill issue cổ điển. Không phải lỗi tay cầm."),
                (70, "Bị toddler và màn hình loading diff."),
                (90, "Tutorial vẫn chưa xong. Bằng cách nào đó."),
                (None, "KỲ DỊ SKILL ISSUE. Bot AFK cũng thắng."),
            ],
        )


        embed = build_meter_embed(
            ctx,
            member,
            title="💀 Skill Issue Meter",
            description=f"Skill issue của {member.mention}",
            percentage=percentage,
            tease=tease,
            footer="Git gud. Hoặc git hài. 💀",
            color=discord.Color.from_rgb(255, 105, 180),
        )
        await loading_message.edit(embed=embed)


async def setup(bot):
    await bot.add_cog(SkillIssueCog(bot))
