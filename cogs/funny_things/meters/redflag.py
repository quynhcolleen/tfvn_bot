import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_number,
    format_signed,
    create_signed_icon_bar,
    pick_tease,
)

RED_FLAG_ICON = "🚩"
GREEN_FLAG_ICON = "🟢"
ZERO_ICON = "⚪"
MAX_FLAGS = 10


def create_flag_icon_bar(flag_score, max_flags=MAX_FLAGS):
    """
    Count-matching icon bar for signed flag score (-10 .. +10).
    1 icon per unit: +score -> 🚩, -score -> 🟢, 0 -> ⚪
    """
    return create_signed_icon_bar(
        flag_score,
        bar_length=max_flags,
        pos_icon=RED_FLAG_ICON,
        neg_icon=GREEN_FLAG_ICON,
        zero_icon=ZERO_ICON,
    )


class RedFlagCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(
        name="redflag",
        aliases=["flags"],
        help="Đếm red flag của một người dùng (-10 green → +10 red).",
    )
    async def redflag_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang đếm red flag... ⏳🚩",
            done_message="Hoàn thành đếm red flag! 🎉",
            emoji="🚩",
        )

        # -10 = full green flag, +10 = full red flag (counts, not percent)
        flag_score = get_daily_number(
            member.id, "redflag", min_value=-MAX_FLAGS, max_value=MAX_FLAGS
        )
        icon_bar = create_flag_icon_bar(flag_score)
        score_display = format_signed(flag_score)

        if flag_score > 0:
            score_label = f"**🚩 Red flag:** {score_display}"
        elif flag_score < 0:
            # Negative score = green flag side (e.g. -10 full green)
            score_label = f"**🟢 Green flag:** {score_display}"
        else:
            score_label = f"**⚪ Trung lập:** {score_display}"

        # Tease by signed score (higher = more red)
        tease = pick_tease(
            flag_score,
            [
                (-7, "Full green flag. Therapist cũng chán mày vì quá ổn."),
                (-3, "Green flag nhẹ. Còn đáng tin... tạm."),
                (1, "Trung lập. Chưa đỏ chưa xanh rõ."),
                (4, "Có mùi red flag. Bạn bè liếc nhau."),
                (8, "Red flag dày. Chạy là cardio và self-care."),
                (None, "TRÙM CUỐI CỜ ĐỎ. +10 full red, không giao chiến."),
            ],
        )

        if flag_score > 0:
            color = discord.Color.from_rgb(220, 50, 50)
        elif flag_score < 0:
            color = discord.Color.from_rgb(46, 204, 113)
        else:
            color = discord.Color.from_rgb(150, 150, 160)

        embed = discord.Embed(
            title="🚩 Red Flag Meter",
            description=f"Số red flag của {member.mention}",
            color=color,
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        # Primary score is signed count only — never N%
        embed.add_field(
            name="Kết quả:",
            value=f"{score_label}\n{icon_bar}\n```{tease}```",
            inline=False,
        )
        embed.set_footer(text="Thang \u221210 (green) \u2192 +10 (red). Phải chịuuuuuu! 🚩")
        await loading_message.edit(content="", embed=embed)


async def setup(bot):
    await bot.add_cog(RedFlagCog(bot))
