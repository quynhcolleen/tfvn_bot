import asyncio
import json
import logging
import random
import re
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

STATUS_FILE = Path(__file__).resolve().parents[2] / "data" / "bot_activity_funny_status.json"
STREAM_URL = "https://www.twitch.tv/discord"
MIN_ROTATION_SECONDS = 5 * 60
MAX_ROTATION_SECONDS = 15 * 60
RETRY_SECONDS = 30
MUTATION_COOLDOWN_SECONDS = 10
NO_MENTIONS = discord.AllowedMentions.none()
PRESENCE_ERRORS = (discord.ConnectionClosed, discord.HTTPException, OSError, RuntimeError)
VALID_ACTIVITY_TYPES = {
    "CUSTOM",
    "PLAYING",
    "WATCHING",
    "LISTENING",
    "STREAMING",
    "COMPETING",
}


def load_statuses(path: Path = STATUS_FILE) -> list[dict[str, str]]:
    """Load valid activity entries from the bot status data file."""
    with path.open("r", encoding="utf-8") as status_file:
        payload: Any = json.load(status_file)

    if not isinstance(payload, dict) or not isinstance(payload.get("bot_statuses"), list):
        raise ValueError("bot_statuses must be a JSON array")

    statuses: list[dict[str, str]] = []
    for entry in payload["bot_statuses"]:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("type"), str)
            or entry["type"].upper() not in VALID_ACTIVITY_TYPES
        ):
            continue

        activity_type = entry["type"].upper()
        status = {"type": activity_type}
        if activity_type == "CUSTOM":
            if (
                not isinstance(entry.get("think"), str)
                or not entry["think"].strip()
            ):
                continue
            status["think"] = entry["think"].strip()
        else:
            if not isinstance(entry.get("text"), str) or not entry["text"].strip():
                continue
            status["text"] = entry["text"].strip()
        statuses.append(status)

    if not statuses:
        raise ValueError("bot_statuses contains no valid activities")
    return statuses


def make_activity(status: dict[str, str]) -> discord.BaseActivity:
    """Convert a status data entry into a Discord activity."""
    activity_type = status["type"]
    if activity_type == "CUSTOM":
        return discord.CustomActivity(name=status["think"])

    text = status["text"]
    if activity_type == "STREAMING":
        return discord.Streaming(name=text, url=STREAM_URL)

    types = {
        "PLAYING": discord.ActivityType.playing,
        "WATCHING": discord.ActivityType.watching,
        "LISTENING": discord.ActivityType.listening,
        "COMPETING": discord.ActivityType.competing,
    }
    return discord.Activity(type=types[activity_type], name=text)


def parse_duration(value: str) -> int:
    """Parse a single duration unit, bounded to one minute through one day."""
    match = re.fullmatch(r"([0-9]{1,5})([mhd])", value.strip().lower())
    if match is None:
        raise ValueError("Thời lượng phải là số nguyên kèm m, h hoặc d, ví dụ 30m, 1h.")
    seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
    if not 60 <= seconds <= 86400:
        raise ValueError("Thời lượng phải từ 1 phút đến 24 giờ.")
    return seconds


def make_override_status(activity_type: str, text: str) -> dict[str, str]:
    """Validate administrator input using the existing activity data schema."""
    activity_type = activity_type.strip().upper()
    if activity_type not in VALID_ACTIVITY_TYPES:
        raise ValueError(
            "Loại trạng thái: PLAYING, WATCHING, LISTENING, STREAMING, COMPETING, CUSTOM."
        )
    text = text.strip()
    if not 1 <= len(text) <= 128 or len(text.splitlines()) != 1:
        raise ValueError("Nội dung phải nằm trên một dòng và dài từ 1 đến 128 ký tự.")
    return {"type": activity_type, "think" if activity_type == "CUSTOM" else "text": text}


@dataclass(frozen=True)
class StatusOverride:
    status: dict[str, str]
    deadline: float
    expires_at: datetime


class BotStatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.statuses = load_statuses()
        self.current_status: dict[str, str] | None = None
        self.override: StatusOverride | None = None
        # Keep random history separate so resetting an override avoids repeats.
        self.last_status: dict[str, str] | None = None
        self._next_rotation_at = 0.0
        self._retry_at = 0.0
        self._refresh_requested = False
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._unloading = False
        self._status_views: weakref.WeakSet[discord.ui.View] = weakref.WeakSet()
        self._mutation_cooldown = commands.Cooldown(1, MUTATION_COOLDOWN_SECONDS)
        self.rotation_task = asyncio.create_task(self.rotate_statuses())

    async def cog_unload(self) -> None:
        self._unloading = True
        for view in tuple(self._status_views):
            view.stop()
        self.rotation_task.cancel()
        try:
            await self.rotation_task
        except asyncio.CancelledError:
            pass
        # Let an already-sent manual update finish before a replacement cog starts.
        async with self._lock:
            pass

    def _choose_random_status(self) -> dict[str, str]:
        choices = [status for status in self.statuses if status != self.last_status]
        return random.choice(choices or self.statuses)

    def _check_mutation_allowed(self) -> None:
        if self._unloading:
            raise RuntimeError("The bot status cog is unloading")
        retry_after = self._mutation_cooldown.update_rate_limit(time.monotonic())
        if retry_after:
            raise commands.CommandOnCooldown(
                self._mutation_cooldown, retry_after, commands.BucketType.default
            )

    async def _apply_status(self, status: dict[str, str]) -> None:
        if self._unloading:
            raise RuntimeError("The bot status cog is unloading")
        if not self.bot.is_ready():
            raise RuntimeError("Discord is not ready for a presence update")
        await self.bot.change_presence(activity=make_activity(status))
        self.current_status = status

    def _schedule_rotation(self, status: dict[str, str]) -> None:
        self.last_status = status
        self._next_rotation_at = time.monotonic() + random.uniform(
            MIN_ROTATION_SECONDS, MAX_ROTATION_SECONDS
        )

    async def set_override(
        self, status: dict[str, str], duration_seconds: int
    ) -> StatusOverride:
        """Apply an override and start its timer only after Discord accepts it."""
        async with self._lock:
            self._check_mutation_allowed()
            await self._apply_status(status)
            override = StatusOverride(
                status=status,
                deadline=time.monotonic() + duration_seconds,
                expires_at=discord.utils.utcnow() + timedelta(seconds=duration_seconds),
            )
            self.override = override
            self._retry_at = 0.0
            self._refresh_requested = False
            self._wake_event.set()
            return override

    async def reset_override(self) -> bool:
        """Restore rotation without discarding an override if the update fails."""
        async with self._lock:
            self._check_mutation_allowed()
            if self.override is None:
                return False
            selected = self._choose_random_status()
            await self._apply_status(selected)
            self.override = None
            self._schedule_rotation(selected)
            self._retry_at = 0.0
            self._refresh_requested = False
            self._wake_event.set()
            return True

    async def update_status(self) -> float:
        """Reconcile the latest timer/connection state and return the next delay."""
        async with self._lock:
            now = time.monotonic()
            if self.override is not None and self.override.deadline <= now:
                self.override = None
                self._next_rotation_at = 0.0

            deadline = (
                self.override.deadline
                if self.override is not None
                else self._next_rotation_at
            )
            if self._retry_at > now:
                delay = self._retry_at - now
                return min(delay, deadline - now) if deadline > now else delay

            rotate = self.override is None and (
                self.current_status is None or self._next_rotation_at <= now
            )
            if not rotate and not self._refresh_requested:
                return max(0.0, deadline - now)

            if self.override is not None:
                selected = self.override.status
            elif rotate:
                selected = self._choose_random_status()
            else:
                selected = self.current_status
            assert selected is not None

            try:
                await self._apply_status(selected)
            except PRESENCE_ERRORS:
                logger.exception("Failed to update the bot activity")
                self._refresh_requested = True
                self._retry_at = time.monotonic() + RETRY_SECONDS
                remaining = deadline - time.monotonic()
                return min(RETRY_SECONDS, remaining) if remaining > 0 else RETRY_SECONDS

            if rotate:
                self._schedule_rotation(selected)
            self._retry_at = 0.0
            self._refresh_requested = False
            deadline = (
                self.override.deadline
                if self.override is not None
                else self._next_rotation_at
            )
            return max(0.0, deadline - time.monotonic())

    async def rotate_statuses(self) -> None:
        while not self.bot.is_closed():
            await self.bot.wait_until_ready()
            self._wake_event.clear()
            delay = await self.update_status()
            try:
                async with asyncio.timeout(delay):
                    await self._wake_event.wait()
            except asyncio.TimeoutError:
                pass

    async def _refresh_presence(self) -> None:
        async with self._lock:
            self._refresh_requested = True
            self._retry_at = 0.0
            self._wake_event.set()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._refresh_presence()

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        await self._refresh_presence()

    @staticmethod
    def _format_status(status: dict[str, str]) -> str:
        text = status["think"] if status["type"] == "CUSTOM" else status["text"]
        return f"{status['type']} — {discord.utils.escape_markdown(text)}"

    @staticmethod
    def _usage(ctx: commands.Context) -> str:
        prefix = ctx.clean_prefix
        return (
            f"Mở bảng điều khiển: `{prefix}bot_status`\n"
            f"`{prefix}bot_status set PLAYING 1h Fortnite`\n"
            f"`{prefix}bot_status show` · `{prefix}bot_status reset`\n"
            "Loại: PLAYING, WATCHING, LISTENING, STREAMING, COMPETING, CUSTOM.\n"
            "Thời lượng: số nguyên + m/h/d, từ 1 phút đến 24 giờ. "
            "Nội dung: một dòng, 1–128 ký tự.\n"
            "Áp dụng cho bot trên mọi server. Trạng thái tạm sẽ mất khi bot khởi động "
            "lại hoặc cog được tải lại. Set/reset dùng chung thời gian chờ 10 giây."
        )

    async def describe_status(self) -> tuple[str, str]:
        """Return the latest applied activity and rotation mode from one snapshot."""
        async with self._lock:
            current = (
                self._format_status(self.current_status)
                if self.current_status is not None
                else "Chưa cập nhật được trạng thái."
            )
            override = self.override
            if override is not None and override.deadline > time.monotonic():
                timestamp = int(override.expires_at.timestamp())
                mode = f"Tùy chỉnh tạm thời · hết hạn <t:{timestamp}:F> (<t:{timestamp}:R>)."
            elif override is not None or self._retry_at > time.monotonic():
                mode = "Đang chờ cập nhật trạng thái; bot sẽ tự thử lại."
            else:
                mode = "Tự động đổi trạng thái ngẫu nhiên mỗi 5–15 phút."
            return current, mode

    async def _show_status(self, ctx: commands.Context) -> None:
        current, mode = await self.describe_status()
        await ctx.send(
            f"Trạng thái đã gửi gần nhất: {current}\n{mode}\n\n{self._usage(ctx)}",
            allowed_mentions=NO_MENTIONS,
        )

    @commands.group(
        name="bot_status",
        invoke_without_command=True,
        help="Mở bảng điều khiển trạng thái bot trên mọi server.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def status_group(self, ctx: commands.Context) -> None:
        from cogs.operation._bot_status_ui import BotStatusView

        view = BotStatusView(
            self, guild_id=ctx.guild.id, author_id=ctx.author.id, prefix=ctx.clean_prefix,
        )
        self._status_views.add(view)
        try:
            view.message = await ctx.send(
                embed=await view.build_embed(), view=view, allowed_mentions=NO_MENTIONS,
            )
        finally:
            if view.message is None:
                view.stop()

    @status_group.command(name="show", help="Xem trạng thái bot và hạn tùy chỉnh.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def show_bot_status(self, ctx: commands.Context) -> None:
        await self._show_status(ctx)

    @status_group.command(name="set", help="Đặt trạng thái bot tạm thời trên mọi server.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def set_bot_status(
        self, ctx: commands.Context, activity_type: str, duration: str, *, text: str
    ) -> None:
        try:
            status = make_override_status(activity_type, text)
            seconds = parse_duration(duration)
        except ValueError as error:
            raise commands.BadArgument(str(error)) from error
        override = await self.set_override(status, seconds)
        timestamp = int(override.expires_at.timestamp())
        await ctx.send(
            f"Đã đặt trạng thái trên mọi server: {self._format_status(status)}\n"
            f"Hết hạn <t:{timestamp}:F> (<t:{timestamp}:R>), sau đó tự đổi ngẫu nhiên.\n"
            f"Dừng sớm: `{ctx.clean_prefix}bot_status reset`.",
            allowed_mentions=NO_MENTIONS,
        )

    @status_group.command(name="reset", help="Xóa trạng thái tạm và bật lại đổi ngẫu nhiên.")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def reset_bot_status(self, ctx: commands.Context) -> None:
        changed = await self.reset_override()
        await ctx.send(
            "Đã xóa trạng thái tạm và khôi phục đổi ngẫu nhiên trên mọi server."
            if changed else "Bot không có trạng thái tạm trên mọi server.",
            allowed_mentions=NO_MENTIONS,
        )

    async def cog_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.NoPrivateMessage):
            message = "Lệnh bot_status chỉ dùng được trong server."
        elif isinstance(error, commands.MissingPermissions):
            message = "Bạn cần quyền Administrator để quản lý trạng thái bot."
        elif isinstance(error, commands.CommandOnCooldown):
            message = f"Hãy thử đổi trạng thái lại sau {error.retry_after:.1f} giây."
        elif isinstance(error, commands.UserInputError):
            detail = str(error) if isinstance(error, commands.BadArgument) else "Sai cú pháp."
            message = f"{detail}\n{self._usage(ctx)}"
        else:
            original = getattr(error, "original", error)
            logger.error(
                "Bot status command failed",
                exc_info=(type(original), original, original.__traceback__),
            )
            message = "Không thể cập nhật trạng thái bot lúc này. Hãy thử lại sau."
        await ctx.send(message, allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotStatusCog(bot))
