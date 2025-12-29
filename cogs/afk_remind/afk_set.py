from datetime import datetime, timedelta

import discord
from discord.ext import commands


class AfkRemindSetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="afk", help="Sets your AFK reminder message.")
    async def set_afk(
        self, ctx: commands.Context, timeout_minutes: str = "5", *, remind_message: str
    ):
        try:
            minutes = int(timeout_minutes)
        except ValueError:
            await ctx.send("Thời gian timeout phải là số nguyên dương (ví dụ: 5).")
            return

        if minutes <= 0:
            await ctx.send("Thời gian timeout phải lớn hơn 0.")
            return

        end_at = datetime.utcnow() + timedelta(minutes=minutes)

        self.db["afk_reminders"].update_one(
            {"user_id": ctx.author.id},
            {"$set": {"message": remind_message, "end_at": end_at}},
            upsert=True,
        )

        await ctx.send(
            f"✅ Đã cài nhắc AFK trong **{minutes} phút**:\n> {remind_message}"
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

    @set_afk.error
    async def set_afk_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "Cách dùng: `!tf afk [phút] <nội dung nhắc>`\n"
                "Ví dụ: `!tf afk 10 Đi ăn cơm`"
            )

    @clear_afk.error
    async def clear_afk_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print(f"[AFK CLEAR ERROR] {error}")
            await ctx.send("Đã xảy ra lỗi khi xóa nhắc AFK.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AfkRemindSetCog(bot))
