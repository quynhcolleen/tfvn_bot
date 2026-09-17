import discord
from discord.ext import commands, tasks

from cogs.funny_things.birthday._birthday_ui import (
    BirthdayView,
    days_in_month,
    is_valid_birthday,
)


class BirthdayCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.birthday_channel_id = self.bot.global_vars.get("BIRTHDAY_CHANNEL")
        if not self.birthday_channel_id:
            raise ValueError("BIRTHDAY_CHANNEL not set in global variables.")
        self.check_birthdays.start()  # Start the daily task

    def cog_unload(self) -> None:
        self.check_birthdays.cancel()  # Stop the task when the cog is unloaded

    @tasks.loop(seconds=5)  # Run every 24 hours
    async def check_birthdays(self) -> None:
        """Daily task to check and announce birthdays."""
        now = discord.utils.utcnow()
        current_date = now.strftime("%Y-%m-%d")  # Format as YYYY-MM-DD for DB
        current_month = now.month
        current_day = now.day

        # Check if birthdays have already been announced today
        if self.db["birthday_announcements"].find_one({"date": current_date}):
            return  # Already announced, skip

        # Query DB for birthdays matching today
        birthdays_today = list(self.db["birthdays"].find({
            "month": current_month,
            "day": current_day
        }))

        if not birthdays_today:
            return  # No birthdays today

        # Get the channel
        channel = self.bot.get_channel(int(self.birthday_channel_id))
        if not channel:
            print("Birthday channel not found.")
            return

        # Send messages for each birthday
        for birthday in birthdays_today:
            user_id = birthday["user_id"]
            user = self.bot.get_user(user_id)
            if user:
                embed = discord.Embed(
                    title="🎉 Chúc Mừng Sinh Nhật! 🎂",
                    description=f"Hôm nay là sinh nhật của {user.mention}! Chúc bạn một ngày vui vẻ và tràn đầy tiếng cười! 🥳",
                    color=discord.Color.gold()
                )
                embed.set_thumbnail(url=user.display_avatar.url)
                await channel.send(embed=embed)
            else:
                await channel.send(f"🎉 Hôm nay là sinh nhật của <@{user_id}>! (Không tìm thấy user)")

        # Mark as announced for today
        self.db["birthday_announcements"].insert_one({"date": current_date, "announced": True})

    def _stored_birthday(self, user_id: int) -> tuple[int, int] | None:
        record = self.db["birthdays"].find_one({"user_id": user_id})
        if not record:
            return None

        month = record.get("month")
        day = record.get("day")
        if not isinstance(month, int) or not isinstance(day, int):
            return None
        if not is_valid_birthday(month, day):
            return None
        return month, day

    def _store_birthday(self, user_id: int, month: int, day: int) -> None:
        if not is_valid_birthday(month, day):
            raise ValueError("Invalid birthday date.")
        self.db["birthdays"].update_one(
            {"user_id": user_id},
            {"$set": {"month": month, "day": day}},
            upsert=True,
        )

    async def _open_birthday_picker(self, ctx: commands.Context) -> None:
        stored_birthday = self._stored_birthday(ctx.author.id)
        initial_month, initial_day = stored_birthday or (None, None)

        async def save_callback(month: int, day: int) -> None:
            self._store_birthday(ctx.author.id, month, day)

        view = BirthdayView(
            author_id=ctx.author.id,
            save_callback=save_callback,
            initial_month=initial_month,
            initial_day=initial_day,
        )
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
            mention_author=False,
        )

    @commands.group(
        name="birthday",
        invoke_without_command=True,
        help="Mở bảng chọn ngày sinh nhật của bạn.",
    )
    async def birthday(self, ctx: commands.Context) -> None:
        """Open the interactive birthday picker."""
        await self._open_birthday_picker(ctx)

    @birthday.command(
        name="set",
        help="Đặt ngày sinh bằng cú pháp birthday set <ngày> <tháng>.",
    )
    async def set_birthday(
        self,
        ctx: commands.Context,
        day: int,
        month: int,
    ) -> None:
        """Set a birthday using the legacy day/month syntax."""
        if not (1 <= month <= 12):
            await ctx.send("Tháng phải từ 1 đến 12.")
            return
        if not is_valid_birthday(month, day):
            await ctx.send(
                f"Ngày phải từ 1 đến {days_in_month(month)} đối với tháng {month}."
            )
            return

        self._store_birthday(ctx.author.id, month, day)
        await ctx.send(f"Đã đặt sinh nhật của bạn là {day}/{month}. 🎂")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BirthdayCog(bot))
