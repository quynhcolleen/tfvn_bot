import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import (
    fake_loading,
    get_daily_number,
    format_signed,
    create_signed_icon_bar,
    pick_tease,
)

# Positive aura sparkles / negative aura (dark)
AURA_ICON_POS = "✨"
AURA_ICON_NEG = "🌑"
AURA_ICON_ZERO = "⚪"
AURA_BAR_LENGTH = 10


def create_aura_icon_bar(aura_points, bar_length=AURA_BAR_LENGTH):
    """
    Count-matching icon bar (1 icon per point, capped at bar_length).
    Not a percent-style scale of the score range.
    +points -> ✨
    -points -> 🌑
     0      -> ⚪
    """
    return create_signed_icon_bar(
        aura_points,
        bar_length=bar_length,
        pos_icon=AURA_ICON_POS,
        neg_icon=AURA_ICON_NEG,
        zero_icon=AURA_ICON_ZERO,
    )


class AuraCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="aura", help="Báo cáo aura (+/- điểm) của một người dùng.")
    async def aura_meter(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author

        loading_message = await fake_loading(
            ctx,
            start_message="Đang quét aura... ⏳✨",
            done_message="Hoàn thành báo cáo aura! 🎉",
            emoji="✨",
        )

        # Aura is a point score from -999 to +999 (not percent)
        aura_points = get_daily_number(member.id, "aura", min_value=-999, max_value=999)

        points_str = format_signed(aura_points)
        icon_bar = create_aura_icon_bar(aura_points)

        tease = pick_tease(
            aura_points,
            [
                (-800, "Aura âm sâu. WiFi cũng né mày."),
                (-400, "Hơi mờ. Main character đang mute."),
                (1, "Trung tính. Không nấu cũng không bị nấu."),
                (400, "Phát sáng dương. Side character nhìn nể."),
                (800, "Aura chói. Chim bay vòng lấy mày làm GPS."),
                (None, "AURA THẦN THÁNH. Thực tại là fanfic về mày."),
            ],
        )

        # Darker embed color when aura is negative
        if aura_points < 0:
            color = discord.Color.from_rgb(40, 40, 60)
        elif aura_points == 0:
            color = discord.Color.from_rgb(150, 150, 160)
        else:
            color = discord.Color.from_rgb(255, 215, 80)

        embed = discord.Embed(
            title="✨ Aura Report",
            description=f"Báo cáo aura của {member.mention}",
            color=color,
        )
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        # Score is signed count only — never N%
        embed.add_field(
            name="Kết quả:",
            value=f"**Điểm aura:** {points_str}\n{icon_bar}\n```{tease}```",
            inline=False,
        )
        embed.set_footer(text="Aura là tạm thời. Screenshot là mãi mãi. ✨")
        await loading_message.edit(content="", embed=embed)


async def setup(bot):
    await bot.add_cog(AuraCog(bot))
