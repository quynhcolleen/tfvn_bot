from datetime import datetime, timedelta
import asyncio
import discord
from discord.ext import commands


class AfkRemindSetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    def parse_time_string(self, time_str: str) -> int:
        total_seconds = 0
        num = ''
        time_units = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}

        for char in time_str:
            if char.isdigit():
                num += char
            elif char in time_units and num:
                total_seconds += int(num) * time_units[char]
                num = ''
            else:
                raise ValueError("Invalid time format")

        if not total_seconds:
            raise ValueError("No valid time found")

        return int(total_seconds)
    
    @commands.command(name="afk", help="Sets your AFK reminder message.")
    async def set_afk(
        self, ctx: commands.Context
    ):
        def check(message):
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            await ctx.send("""
                           Bạn muốn AFK trong bao nhiêu lâu?
                           ```
                           Vui lòng nhập theo định dạng như sau 
                           <1 số nguyên> + d đại diện cho ngày (bỏ qua nếu không cần),
                           <1 số nguyên> + h đại diện cho giờ (bỏ qua nếu không cần),
                           <1 số nguyên> + m đại diện cho phút (bỏ qua nếu không cần)
                           <1 số nguyên> + s đại diện cho giây (bỏ qua nếu không cần)
                           Ví dụ: 1h30m, 45m, 2h, 10s, 1d2h, 1d2h3m4s, 1d 2h 3m 4s
                           ```
                           """)
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            time_str = msg.content.lower().replace(" ", "")
        except asyncio.TimeoutError:
            await ctx.send("Hết thời gian chờ. Vui lòng thử lại.")
        except ValueError:
            await ctx.send("Định dạng không hợp lệ.")

        try:
            await ctx.send("""
                           Bạn muốn nhắc nhở về điều gì khi AFK?
                           Vui lòng nhập nội dung nhắc nhở.
                            """)
            msg_afk_message = await self.bot.wait_for("message", check=check, timeout=300)
            remind_message = msg_afk_message.content
            print(f"remind_message: {remind_message}")
        except asyncio.TimeoutError:
            await ctx.send("Hết thời gian chờ. Vui lòng thử lại.")
        
        try:
            seconds = self.parse_time_string(time_str)
        except Exception as e:
            await ctx.send("Định dạng thời gian không hợp lệ. Vui lòng thử lại.")
            return
        
        end_at = datetime.utcnow() + timedelta(seconds=seconds)

        self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id},
            {"$set": {"message": remind_message, "end_at": end_at}},
            upsert=True,
        )

        await ctx.send(
            f"✅ Đã cài nhắc AFK trong **{seconds} giây**:\n> {remind_message}"
        )

    @commands.command(name="clear_afk", help="Clears your AFK reminder message.")
    async def clear_afk(self, ctx: commands.Context):
        result = self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id, "end_at": {"$gt": datetime.utcnow()}},
            {"$set": {"end_at": datetime.utcnow()}},
        )

        if result.matched_count == 0:
            await ctx.send("Bạn chưa cài nhắc AFK nào.")
            return

        await ctx.send("✅ Đã xóa nhắc AFK của bạn.")

    @clear_afk.error
    async def clear_afk_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print(f"[AFK CLEAR ERROR] {error}")
            await ctx.send("Đã xảy ra lỗi khi xóa nhắc AFK.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AfkRemindSetCog(bot))
