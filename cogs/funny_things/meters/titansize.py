import datetime
import random

import discord
from discord.ext import commands

from cogs.funny_things.meters._meter_helper import create_progress_bar, fake_loading


CUP_SIZES = ("A", "B", "C", "D", "DD", "E", "F", "G", "H")
MIN_SIZE_CM = 60.0
MAX_SIZE_CM = 130.0
CUP_TEASES = {
    "A": "Chế độ gọn nhẹ đã được kích hoạt.",
    "B": "Máy đo báo chỉ số cân bằng.",
    "C": "Nằm ngay giữa bảng xếp hạng hôm nay.",
    "D": "Thước đo bắt đầu xin thêm pin.",
    "DD": "Hệ thống phải đo lại cho chắc.",
    "E": "Thước dây vừa gửi đơn xin tăng ca.",
    "F": "Dữ liệu sắp vượt khung hiển thị.",
    "G": "Máy đo đang cân nhắc đầu hàng.",
    "H": "Kết quả cấp huyền thoại đã xuất hiện.",
}


def get_daily_measurement(
    user_id: int,
    day: datetime.date | None = None,
) -> tuple[float, str]:
    """Return a deterministic fictional size and cup for one member and day."""
    measured_day = day or datetime.date.today()
    rng = random.Random(f"titansize-{user_id}-{measured_day.isoformat()}")
    size_cm = rng.randint(int(MIN_SIZE_CM * 10), int(MAX_SIZE_CM * 10)) / 10
    return size_cm, rng.choice(CUP_SIZES)


def describe_cup_size(cup_size: str) -> str:
    """Return the playful tease for a supported fictional cup size."""
    try:
        return CUP_TEASES[cup_size]
    except KeyError as exc:
        raise ValueError(f"Unsupported cup size: {cup_size}") from exc


class TitanSizeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(
        name="titansize",
        help="Đo số đo cm và cup hư cấu của một thành viên.",
    )
    async def titan_size(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author
        loading_message = await fake_loading(
            ctx,
            "Đang lấy thước dây Tit-an... ⏳",
            done_message="Đo Tit-an xong rồi đây! 🎉",
            emoji="📏",
        )

        size_cm, cup_size = get_daily_measurement(target.id)
        tease = describe_cup_size(cup_size)
        percentage = (CUP_SIZES.index(cup_size) + 1) / len(CUP_SIZES) * 100
        progress_bar = create_progress_bar(percentage)

        embed = discord.Embed(
            title="📏 Tit-an Size Meter 📏",
            description=f"{target.mention}\n```\n（ ͜.人 ͜.）\n```",
            color=discord.Color.dark_teal(),
        )
        embed.set_author(
            name=ctx.author.name,
            icon_url=ctx.author.display_avatar.url,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Kết quả:",
            value=(
                f"{progress_bar}\n"
                f"**Số đo:** {size_cm:.1f} cm\n"
                f"**Cup:** {cup_size}\n"
                f"```{tease}```"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Kết quả ngẫu nhiên hư cấu, không phải số đo thật."
        )

        await loading_message.edit(
            content="Đo Tit-an xong rồi đây! 🎉",
            embed=embed,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TitanSizeCog(bot))
