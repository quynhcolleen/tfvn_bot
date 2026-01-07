import asyncio
from datetime import timedelta
from tabnanny import check
import discord
from discord.ext import commands, tasks
import re

class JobRemind(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_task.start()
        self.db = bot.db  # Assuming the bot has a db attribute for database access

    def task_record_exists(self, task_id):
        """Check if a task record exists in the database."""
        return self.db["tasks"].find_one({"task_id": task_id}) is not None
    
    def create_task_record(self, task_id, user_id, job_name, remind_time):
        """Create a new task record in the database."""
        self.db["tasks"].insert_one({
            "task_id": task_id,
            "user_id": user_id,
            "job_name": job_name,
            "remind_time": remind_time
        })

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

    @commands.group(name='jobremind', invoke_without_command=True)
    async def jobremind(self, ctx):
        """Group command for job reminders."""
        await ctx.send("Sử dụng các lệnh con để quản lý nhắc nhở công việc.")

    @jobremind.command(name='add')
    async def add_reminder(self, ctx, ):
        # check if it invoked by the same user in the same channel
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        """Add a job reminder."""
        try:
            embed = discord.Embed(
                title="Nhắc trong bao nhiêu lâu nữa",
                description="Vui lòng nhập thời gian nhắc:\nVí dụ: `1h30m`, `2d 3h 5s`",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="!tf jobremind add")
            await ctx.send(embed=embed)

            msg_time = await self.bot.wait_for("message", check=check, timeout=120)
            time_str = msg_time.content.lower().replace(" ", "")
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            embed = discord.Embed(
                title="Nhắc nhở công việc gì?",
                description="Vui lòng nhập tên công việc này:",
                color=discord.Color.blurple(),
            )
            await ctx.send(embed=embed)

            msg_job_name = await self.bot.wait_for(
                "message", check=check, timeout=300
            )
            job_name = msg_job_name.content
        except asyncio.TimeoutError:
            await ctx.send("⏰ Hết thời gian chờ. Vui lòng thử lại.")
            return

        try:
            seconds = self.parse_time_string(time_str)
        except Exception:
            await ctx.send("❌ Định dạng thời gian không hợp lệ. Vui lòng thử lại.")
            return

        task_id = f"{ctx.author.id}-{job_name}-{time_str}"

        if self.task_record_exists(task_id):
            await ctx.send("Nhắc nhở cho công việc này đã tồn tại.")
        else:
            remind_time = (discord.utils.utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M")
            self.create_task_record(task_id, ctx.author.id, job_name, remind_time)
            await ctx.send(f"Nhắc nhở công việc '{job_name}' đã được đặt cho {remind_time}.")

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        """Periodic task to check and send job reminders."""
        current_time = discord.utils.utcnow().strftime("%Y-%m-%d %H:%M")
        reminders = self.db.tasks.find({"remind_time": current_time})
        for reminder in reminders:
            user = self.bot.get_user(reminder["user_id"])
            if user:
                await user.send(f"⏰ Nhắc nhở công việc cho <@{reminder['user_id']}>: {reminder['job_name']}")
                # Optionally, remove the reminder after sending
                self.db["tasks"].delete_one({"task_id": reminder["task_id"]})
    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(JobRemind(bot))