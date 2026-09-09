import discord
from discord.ext import commands

from cogs.operation._doctor import collect_doctor_checks
from cogs.operation._setup_helpers import SetupCheck, summarize_checks


class SetupCheckCog(commands.Cog):
    """Read-only diagnostics for configuration, IDs, and Discord permissions."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _result_lines(checks: list[SetupCheck], level: str) -> str:
        icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}[level]
        selected = [check for check in checks if check.level == level]
        lines = []
        for check in selected[:12]:
            line = f"{icon} **{check.name}:** {check.detail}"
            if check.fix:
                line += f" Fix: {check.fix}"
            lines.append(line)
        if len(selected) > 12:
            lines.append(f"… và {len(selected) - 12} mục khác.")
        return "\n".join(lines) or "Không có."

    @commands.group(
        name="setup",
        aliases=["diagnose"],
        invoke_without_command=True,
        help="Kiểm tra cấu hình server.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @commands.cooldown(1, 15, commands.BucketType.guild)
    async def setup_group(self, ctx: commands.Context) -> None:
        await self.run_setup_check(ctx)

    @setup_group.command(name="check")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def setup_check(self, ctx: commands.Context) -> None:
        await self.run_setup_check(ctx)

    async def run_setup_check(self, ctx: commands.Context) -> None:
        checks = await collect_doctor_checks(self.bot, ctx.guild, ctx.channel)
        totals = summarize_checks(checks)
        color = (
            discord.Color.red()
            if totals["error"]
            else discord.Color.orange()
            if totals["warning"]
            else discord.Color.green()
        )
        embed = discord.Embed(
            title="🩺 TFVN setup check",
            description=(
                f"✅ {totals['ok']} · ⚠️ {totals['warning']} · ❌ {totals['error']}"
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Lỗi cần sửa",
            value=self._result_lines(checks, "error")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Cảnh báo",
            value=self._result_lines(checks, "warning")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Đã đạt",
            value=self._result_lines(checks, "ok")[:1024],
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @setup_group.error
    async def setup_group_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Vui lòng thử lại sau {error.retry_after:.0f} giây.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Server để chạy setup check.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCheckCog(bot))
