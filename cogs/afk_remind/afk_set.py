import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import re


class AFK(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    def parse_time_string(self, time_str: str) -> int:
        pattern = r"(\d+)([dhms])"
        matches = re.findall(pattern, time_str)

        if not matches:
            raise ValueError("Invalid time format")

        total_seconds = 0
        for value, unit in matches:
            value = int(value)
            if unit == "d":
                total_seconds += value * 86400
            elif unit == "h":
                total_seconds += value * 3600
            elif unit == "m":
                total_seconds += value * 60
            elif unit == "s":
                total_seconds += value

        return total_seconds

    def format_duration(self, seconds: int) -> str:
        units = [
            ("ngày", 86400),
            ("giờ", 3600),
            ("phút", 60),
            ("giây", 1),
        ]

        parts = []
        for name, unit_seconds in units:
            value, seconds = divmod(seconds, unit_seconds)
            if value > 0:
                parts.append(f"{value} {name}")

        return " ".join(parts)

    @commands.command(name="afk")
    async def afk(self, ctx):
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            embed = discord.Embed(
                title="Set thời gian AFK ⌛",
                description="Vui lòng nhập thời gian muốn AFK:\nVí dụ: `1h30m`, `2d 3h 5s`",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="!tf afk")
            await ctx.send(embed=embed)

            msg_time = await self.bot.wait_for("message", check=check, timeout=120)
            time_str = msg_time.content.lower().replace(" ", "")
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            embed = discord.Embed(
                title="Set tin nhắn nhắc AFK 📝",
                description="Vui lòng nhập lý do AFK:",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg_afk_message = await self.bot.wait_for(
                "message", check=check, timeout=300
            )
            remind_message = msg_afk_message.content
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            seconds = self.parse_time_string(time_str)
        except Exception:
            await ctx.send("❌ Định dạng thời gian không hợp lệ. Vui lòng thử lại.")
            return

        formatted_time = self.format_duration(seconds)
        end_at = datetime.utcnow() + timedelta(seconds=seconds)

        self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id},
            {"$set": {"message": remind_message, "end_at": end_at}},
            upsert=True,
        )

        embed = discord.Embed(
            title="✅ Đã set nhắc AFK!",
            description=(
                f"**Thời gian**: {formatted_time}.\n**Lý do:** {remind_message}\n\nNhập `!tf afk clear` để hủy nhắc AFK."
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="afk clear")
    async def clear_afk(self, ctx: commands.Context):
        result = self.db["afk_reminders"].update_one(
            {
                "user_id": ctx.author.id,
                "end_at": {"$gt": datetime.utcnow()},
            },
            {
                "$set": {"end_at": datetime.utcnow()},
            },
        )

        if result.matched_count == 0:
            embed = discord.Embed(
                description="❌ Bạn chưa cài lời nhắc AFK nào.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(
            description="✅ Đã xóa lời nhắc AFK của bạn.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @clear_afk.error
    async def clear_afk_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandInvokeError):
            print(f"[AFK CLEAR ERROR] {error}")

            embed = discord.Embed(
                description="⚠️ Đã xảy ra lỗi khi xóa lời nhắc AFK.",
                color=discord.Color.orange(),
            )
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AFK(bot))
