import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import discord
from discord.ext import commands

from assets.gifs import BANNED_GIF, GOODBYE_GIF
from cogs.announcement._media import announcement_gif_url
from cogs.settings._guild_variables import get_guild_variable


logger = logging.getLogger(__name__)

AUDIT_LOOKUP_ATTEMPTS = 2
AUDIT_LOOKUP_DELAY_SECONDS = 0.75
EVENT_GRACE_SECONDS = 0.75
DEPARTURE_SIGNAL_WINDOW_SECONDS = 15


class DepartureKind(Enum):
    LEAVE = "leave"
    KICK = "kick"
    BAN = "ban"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DepartureSignal:
    kind: DepartureKind
    occurred_at: datetime


def _target_id(entry: discord.AuditLogEntry) -> int | None:
    target = getattr(entry, "target", None)
    target_id = getattr(target, "id", None)
    return target_id if isinstance(target_id, int) else None


def _is_recent(
    occurred_at: datetime,
    reference_at: datetime,
    *,
    window_seconds: float = DEPARTURE_SIGNAL_WINDOW_SECONDS,
) -> bool:
    return abs((reference_at - occurred_at).total_seconds()) <= window_seconds


def build_departure_embed(
    member: discord.Member | discord.User,
    kind: DepartureKind,
    *,
    goodbye_gif: str = GOODBYE_GIF,
    banned_gif: str = BANNED_GIF,
) -> discord.Embed:
    if kind is DepartureKind.BAN:
        title = f"{member.name} đã ăn sút và cút 🔨"
        color = discord.Color.red()
        image_url = banned_gif
    elif kind is DepartureKind.KICK:
        title = f"{member.name} đã ăn kick và cút 👢"
        color = discord.Color.orange()
        image_url = goodbye_gif
    elif kind is DepartureKind.LEAVE:
        title = f"{member.name} đã rời khỏi server 🥹"
        color = discord.Color.blue()
        image_url = goodbye_gif
    else:
        title = f"{member.name} đã rời hoặc bị đưa khỏi server 👋"
        color = discord.Color.light_grey()
        image_url = goodbye_gif

    embed = discord.Embed(title=title, color=color)
    avatar_url = member.display_avatar.url
    embed.set_author(name=member.name, icon_url=avatar_url)
    embed.set_thumbnail(url=avatar_url)
    if image_url:
        embed.set_image(url=image_url)
    return embed


class GoodbyeCog(commands.Cog):
    """Send exactly one classified announcement for each member departure."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._departure_signals: dict[tuple[int, int], DepartureSignal] = {}

    def _remember_departure(
        self,
        guild_id: int,
        member_id: int,
        kind: DepartureKind,
        occurred_at: datetime | None = None,
    ) -> None:
        now = discord.utils.utcnow()
        cutoff = now - timedelta(seconds=DEPARTURE_SIGNAL_WINDOW_SECONDS)
        self._departure_signals = {
            key: signal
            for key, signal in self._departure_signals.items()
            if signal.occurred_at >= cutoff
        }
        self._departure_signals[(guild_id, member_id)] = DepartureSignal(
            kind=kind,
            occurred_at=occurred_at or now,
        )

    def _consume_departure(
        self,
        guild_id: int,
        member_id: int,
        reference_at: datetime,
    ) -> DepartureKind | None:
        signal = self._departure_signals.pop((guild_id, member_id), None)
        if signal is None or not _is_recent(signal.occurred_at, reference_at):
            return None
        return signal.kind

    @staticmethod
    def _can_view_audit_log(guild: discord.Guild) -> bool:
        bot_member = guild.me
        return bool(
            bot_member
            and getattr(bot_member.guild_permissions, "view_audit_log", False)
        )

    async def _recent_kick_from_audit_log(
        self,
        guild: discord.Guild,
        member_id: int,
        reference_at: datetime,
    ) -> tuple[DepartureKind | None, bool]:
        after = reference_at - timedelta(
            seconds=DEPARTURE_SIGNAL_WINDOW_SECONDS
        )
        try:
            entries = guild.audit_logs(
                limit=5,
                action=discord.AuditLogAction.kick,
                after=after,
                oldest_first=False,
            )
            async for entry in entries:
                occurred_at = entry.created_at
                if (
                    _target_id(entry) == member_id
                    and _is_recent(occurred_at, reference_at)
                ):
                    return DepartureKind.KICK, True
        except discord.Forbidden:
            logger.warning(
                "Missing audit-log access while classifying departure "
                "guild=%s member=%s",
                guild.id,
                member_id,
            )
            return None, False
        except discord.HTTPException:
            logger.exception(
                "Discord audit-log lookup failed guild=%s member=%s",
                guild.id,
                member_id,
            )
            return None, False
        return None, True

    async def _classify_departure(
        self,
        member: discord.Member,
        reference_at: datetime,
    ) -> DepartureKind:
        guild = member.guild
        kind = self._consume_departure(guild.id, member.id, reference_at)
        if kind is not None:
            return kind

        await asyncio.sleep(EVENT_GRACE_SECONDS)
        kind = self._consume_departure(guild.id, member.id, reference_at)
        if kind is not None:
            return kind

        if not self._can_view_audit_log(guild):
            return DepartureKind.UNKNOWN

        for attempt in range(AUDIT_LOOKUP_ATTEMPTS):
            kind, audit_available = await self._recent_kick_from_audit_log(
                guild,
                member.id,
                reference_at,
            )
            if kind is not None:
                return kind
            if not audit_available:
                return DepartureKind.UNKNOWN

            kind = self._consume_departure(guild.id, member.id, reference_at)
            if kind is not None:
                return kind
            if attempt < AUDIT_LOOKUP_ATTEMPTS - 1:
                await asyncio.sleep(AUDIT_LOOKUP_DELAY_SECONDS)

        kind = self._consume_departure(guild.id, member.id, reference_at)
        return kind or DepartureKind.LEAVE

    async def send_departure(
        self,
        member: discord.Member | discord.User,
        channel: discord.abc.Messageable,
        kind: DepartureKind,
    ) -> None:
        await channel.send(
            embed=build_departure_embed(
                member,
                kind,
                goodbye_gif=announcement_gif_url(
                    self.bot, channel.guild.id, "GOODBYE_GIF_URL", GOODBYE_GIF
                ),
                banned_gif=announcement_gif_url(
                    self.bot, channel.guild.id, "BANNED_GIF_URL", BANNED_GIF
                ),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ) -> None:
        self._remember_departure(guild.id, user.id, DepartureKind.BAN)

    @commands.Cog.listener()
    async def on_audit_log_entry_create(
        self,
        entry: discord.AuditLogEntry,
    ) -> None:
        kind = {
            discord.AuditLogAction.kick: DepartureKind.KICK,
            discord.AuditLogAction.ban: DepartureKind.BAN,
        }.get(entry.action)
        target_id = _target_id(entry)
        if kind is None or target_id is None:
            return
        self._remember_departure(
            entry.guild.id,
            target_id,
            kind,
            entry.created_at,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self._departure_signals.pop((member.guild.id, member.id), None)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        try:
            channel_id = int(get_guild_variable(self.bot, member.guild.id, "BYE_CHANNEL"))
        except (TypeError, ValueError):
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None or getattr(getattr(channel, "guild", None), "id", None) != member.guild.id:
            logger.warning(
                "Goodbye channel is unavailable channel=%s guild=%s",
                channel_id,
                member.guild.id,
            )
            return

        kind = await self._classify_departure(
            member,
            discord.utils.utcnow(),
        )
        try:
            await self.send_departure(member, channel, kind)
        except discord.Forbidden:
            logger.warning(
                "Cannot send departure announcement channel=%s guild=%s",
                channel_id,
                member.guild.id,
            )
        except discord.HTTPException:
            logger.exception(
                "Departure announcement failed channel=%s guild=%s",
                channel_id,
                member.guild.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GoodbyeCog(bot))
