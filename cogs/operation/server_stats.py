import discord
from discord.ext import commands
from datetime import datetime

class ServerStatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = datetime.utcnow()  # Log start time
        self.command_count = 0  # Total commands executed
        self.exception_count = 0  # Total exceptions caused

    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Increment command count on each command execution."""
        self.command_count += 1

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Increment exception count on command errors."""
        self.exception_count += 1

    @commands.command(name='server_stats', help='Displays server stats since bot start.')
    @commands.has_permissions(administrator=True)
    @commands.cooldown(1, 10, commands.BucketType.guild)  # 10 seconds cooldown per guild
    async def server_stats(self, ctx):
        """Displays the bot's start time, command count, and exception count."""
        uptime = datetime.utcnow() - self.start_time
        await ctx.send(
            f"**Thống kê máy chủ:**\n"
            f"- Khởi chạy lúc: {self.start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"- Thời gian hoạt động: {uptime.days} ngày, {uptime.seconds // 3600} giờ, {(uptime.seconds % 3600) // 60} phút\n"
            f"- Lệnh đã thực thi: {self.command_count}\n"
            f"- Lệnh lỗi: {self.exception_count}"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(ServerStatsCog(bot))