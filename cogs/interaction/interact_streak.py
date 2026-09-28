from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.interaction._interact_streak_helpers import (
    SOURCE_VOICE,
    STREAK_LIST_LIMIT,
    VOICE_OVERLAP_SECONDS,
    VALID_SOURCES,
    StreakCreditResult,
    apply_streak_credit,
    displayed_current_streak,
    format_milestone_message,
    is_credit_eligible_voice_channel,
    is_streak_milestone,
    iso_date,
    live_streak_query,
    normalize_pair,
    other_user_id,
    partners_from_message,
    previous_iso_date,
    source_label,
    vietnam_date,
    voice_overlap_partner_ids,
)


logger = logging.getLogger(__name__)

STREAKS_COLLECTION = "interaction_streaks"
STREAK_COOLDOWN_RATE = 1
STREAK_COOLDOWN_PER = 5.0
STREAK_EMBED_COLOR = 0xEB459E
NO_MENTIONS = discord.AllowedMentions.none()
MILESTONE_MENTIONS = discord.AllowedMentions(
    everyone=False,
    users=True,
    roles=False,
)

VoiceTaskKey = tuple[int, int, int, int]


class InteractStreakCog(commands.Cog):
    """Guild-scoped pair streaks from mentions, replies, and shared voice time."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        voice_overlap_seconds: int = VOICE_OVERLAP_SECONDS,
    ) -> None:
        self.bot = bot
        self.db = bot.db
        self.collection = bot.db[STREAKS_COLLECTION]
        self.voice_overlap_seconds = voice_overlap_seconds
        self._pending_voice: dict[VoiceTaskKey, asyncio.Task[None]] = {}
        self._credited_today: set[tuple[int, int, int, str]] = set()
        self._credited_today_date: str | None = None
        self._ensure_indexes()

    def cog_unload(self) -> None:
        for task in list(self._pending_voice.values()):
            task.cancel()
        self._pending_voice.clear()

    def _ensure_indexes(self) -> None:
        try:
            self.collection.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("user_a", ASCENDING),
                    ("user_b", ASCENDING),
                ],
                unique=True,
                name="guild_streak_pair_unique",
            )
            self.collection.create_index(
                [("guild_id", ASCENDING), ("partner_ids", ASCENDING)],
                name="guild_streak_partners",
            )
            self.collection.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("current_streak", DESCENDING),
                    ("last_active_date", DESCENDING),
                ],
                name="guild_streak_leaderboard",
            )
        except PyMongoError:
            logger.exception("Failed to create interaction streak indexes")

    def _vietnam_today(self, now: datetime | None = None) -> str:
        stamp = now if now is not None else discord.utils.utcnow()
        return iso_date(vietnam_date(stamp))

    def _rotate_credit_cache(self, today: str) -> None:
        if self._credited_today_date != today:
            self._credited_today.clear()
            self._credited_today_date = today

    def _credit_cache_key(
        self, guild_id: int, user_a: int, user_b: int, today: str
    ) -> tuple[int, int, int, str]:
        return (guild_id, user_a, user_b, today)

    def _is_credited_today(
        self, guild_id: int, user_a: int, user_b: int, today: str
    ) -> bool:
        self._rotate_credit_cache(today)
        return self._credit_cache_key(guild_id, user_a, user_b, today) in (
            self._credited_today
        )

    def _mark_credited_today(
        self, guild_id: int, user_a: int, user_b: int, today: str
    ) -> None:
        self._rotate_credit_cache(today)
        self._credited_today.add(
            self._credit_cache_key(guild_id, user_a, user_b, today)
        )

    def credit_pair(
        self,
        guild_id: int,
        user_id_a: int,
        user_id_b: int,
        source: str,
        *,
        now: datetime | None = None,
    ) -> StreakCreditResult | None:
        """Credit one pair for the Vietnam day. `advanced` is True only for this writer."""
        if source not in VALID_SOURCES:
            return None
        try:
            user_a, user_b = normalize_pair(user_id_a, user_id_b)
        except ValueError:
            return None

        stamp = now if now is not None else discord.utils.utcnow()
        today = self._vietnam_today(stamp)
        if self._is_credited_today(guild_id, user_a, user_b, today):
            return None

        pair_filter = {
            "guild_id": guild_id,
            "user_a": user_a,
            "user_b": user_b,
        }
        try:
            existing = self.collection.find_one(pair_filter)
            result = apply_streak_credit(
                existing,
                guild_id=guild_id,
                user_id_a=user_a,
                user_id_b=user_b,
                today=today,
                yesterday=previous_iso_date(today),
                source=source,
                now=stamp,
            )
            if not result.advanced:
                self._mark_credited_today(guild_id, user_a, user_b, today)
                return result

            cas_filter = dict(pair_filter)
            if existing is not None:
                cas_filter["last_active_date"] = existing.get("last_active_date")
            payload = {
                key: value
                for key, value in result.document.items()
                if key != "_id"
            }
            updated = self.collection.find_one_and_update(
                cas_filter,
                {"$set": payload},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            self._mark_credited_today(guild_id, user_a, user_b, today)
            try:
                stored = self.collection.find_one(pair_filter)
            except PyMongoError:
                logger.exception("Failed to reload interaction streak after race")
                return None
            if stored is None:
                return None
            return StreakCreditResult(stored, advanced=False)
        except PyMongoError:
            logger.exception("Failed to credit interaction streak")
            return None

        if updated is None:
            try:
                stored = self.collection.find_one(pair_filter)
            except PyMongoError:
                logger.exception("Failed to reload interaction streak after CAS miss")
                return None
            if stored is not None:
                self._mark_credited_today(guild_id, user_a, user_b, today)
                return StreakCreditResult(stored, advanced=False)
            return None

        self._mark_credited_today(guild_id, user_a, user_b, today)
        return StreakCreditResult(updated, advanced=True)

    async def _announce_milestone_if_needed(
        self,
        channel: object | None,
        result: StreakCreditResult | None,
    ) -> None:
        if result is None or not result.advanced:
            return
        document = result.document
        current = int(document.get("current_streak") or 0)
        if not is_streak_milestone(current):
            return
        send = getattr(channel, "send", None)
        if not callable(send):
            return
        user_a = int(document["user_a"])
        user_b = int(document["user_b"])
        try:
            await send(
                format_milestone_message(user_a, user_b, current),
                allowed_mentions=MILESTONE_MENTIONS,
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.info(
                "Could not announce streak milestone guild=%s pair=%s-%s days=%s",
                document.get("guild_id"),
                user_a,
                user_b,
                current,
            )

    def _afk_channel_id(self, guild: discord.Guild) -> int | None:
        afk = guild.afk_channel
        return None if afk is None else afk.id

    def _is_afk_channel(
        self, guild: discord.Guild, channel: discord.abc.GuildChannel | None
    ) -> bool:
        if channel is None:
            return False
        return not is_credit_eligible_voice_channel(
            channel.id,
            self._afk_channel_id(guild),
        )

    def _pair_already_credited_today(
        self, guild_id: int, user_a: int, user_b: int, today: str
    ) -> bool:
        if self._is_credited_today(guild_id, user_a, user_b, today):
            return True
        try:
            existing = self.collection.find_one(
                {
                    "guild_id": guild_id,
                    "user_a": user_a,
                    "user_b": user_b,
                    "last_active_date": today,
                }
            )
        except PyMongoError:
            logger.exception("Failed to check interaction streak credit cache")
            return False
        if existing is None:
            return False
        self._mark_credited_today(guild_id, user_a, user_b, today)
        return True

    def _still_together(
        self,
        guild_id: int,
        channel_id: int,
        left_id: int,
        right_id: int,
    ) -> bool:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return False
        channel = guild.get_channel(channel_id)
        if channel is None or self._is_afk_channel(guild, channel):
            return False
        humans = {
            member.id
            for member in getattr(channel, "members", ())
            if isinstance(getattr(member, "id", None), int)
            and not bool(getattr(member, "bot", False))
        }
        return left_id in humans and right_id in humans

    def _cancel_voice_for_member(
        self, guild_id: int, channel_id: int, user_id: int
    ) -> None:
        keys = [
            key
            for key in self._pending_voice
            if key[0] == guild_id
            and key[1] == channel_id
            and (key[2] == user_id or key[3] == user_id)
        ]
        for key in keys:
            task = self._pending_voice.pop(key, None)
            if task is not None and not task.done():
                task.cancel()

    def _start_voice_overlaps(
        self,
        member: discord.Member,
        channel: discord.abc.GuildChannel,
    ) -> None:
        guild = member.guild
        if self._is_afk_channel(guild, channel):
            return
        today = self._vietnam_today()
        occupants = getattr(channel, "members", ())
        for partner_id in voice_overlap_partner_ids(member.id, occupants):
            try:
                user_a, user_b = normalize_pair(member.id, partner_id)
            except ValueError:
                continue
            key = (guild.id, channel.id, user_a, user_b)
            if key in self._pending_voice:
                continue
            if self._pair_already_credited_today(guild.id, user_a, user_b, today):
                continue
            task = asyncio.create_task(
                self._complete_voice_overlap(
                    guild.id,
                    channel.id,
                    user_a,
                    user_b,
                )
            )
            self._pending_voice[key] = task

    async def _complete_voice_overlap(
        self,
        guild_id: int,
        channel_id: int,
        user_a: int,
        user_b: int,
    ) -> None:
        key = (guild_id, channel_id, user_a, user_b)
        try:
            if self.voice_overlap_seconds > 0:
                await asyncio.sleep(self.voice_overlap_seconds)
            if not self._still_together(guild_id, channel_id, user_a, user_b):
                return
            result = self.credit_pair(guild_id, user_a, user_b, SOURCE_VOICE)
            guild = self.bot.get_guild(guild_id)
            channel = None if guild is None else guild.get_channel(channel_id)
            await self._announce_milestone_if_needed(channel, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Voice streak overlap failed guild=%s channel=%s pair=%s-%s",
                guild_id,
                channel_id,
                user_a,
                user_b,
            )
        finally:
            if self._pending_voice.get(key) is asyncio.current_task():
                self._pending_voice.pop(key, None)

    def _resync_voice_sessions(self) -> None:
        for guild in self.bot.guilds:
            afk_id = self._afk_channel_id(guild)
            channels: list[discord.abc.GuildChannel] = []
            channels.extend(getattr(guild, "voice_channels", ()) or ())
            channels.extend(getattr(guild, "stage_channels", ()) or ())
            for channel in channels:
                if not is_credit_eligible_voice_channel(channel.id, afk_id):
                    continue
                for member in getattr(channel, "members", ()):
                    if bool(getattr(member, "bot", False)):
                        continue
                    self._start_voice_overlaps(member, channel)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self._resync_voice_sessions()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
        ):
            return

        reply_author = None
        reference = message.reference
        if reference is not None:
            resolved = getattr(reference, "resolved", None)
            reply_author = getattr(resolved, "author", None)

        partners = partners_from_message(
            author_id=message.author.id,
            mentions=message.mentions,
            reply_author=reply_author,
        )
        if not partners:
            return

        now = discord.utils.utcnow()
        for partner in partners:
            result = self.credit_pair(
                message.guild.id,
                message.author.id,
                partner.user_id,
                partner.source,
                now=now,
            )
            await self._announce_milestone_if_needed(
                getattr(message, "channel", None),
                result,
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or member.guild is None:
            return
        old_channel = before.channel
        new_channel = after.channel
        if old_channel == new_channel:
            return
        if old_channel is not None:
            self._cancel_voice_for_member(
                member.guild.id,
                old_channel.id,
                member.id,
            )
        if new_channel is not None:
            self._start_voice_overlaps(member, new_channel)

    @commands.group(
        name="streak",
        invoke_without_command=True,
        help="Xem chuỗi tương tác với member khác. Subcommands: top.",
    )
    @commands.guild_only()
    @commands.cooldown(
        STREAK_COOLDOWN_RATE,
        STREAK_COOLDOWN_PER,
        commands.BucketType.user,
    )
    async def streak(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        assert ctx.guild is not None
        if member is None:
            await self._send_my_streaks(ctx)
            return
        await self._send_pair_streak(ctx, member)

    @streak.command(name="top", help="Bảng xếp hạng chuỗi tương tác đang sống.")
    @commands.guild_only()
    @commands.cooldown(
        STREAK_COOLDOWN_RATE,
        STREAK_COOLDOWN_PER,
        commands.BucketType.user,
    )
    async def streak_top(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        today = self._vietnam_today()
        query = live_streak_query(ctx.guild.id, today)
        try:
            top = list(
                self.collection.find(query)
                .sort(
                    [
                        ("current_streak", DESCENDING),
                        ("longest_streak", DESCENDING),
                    ]
                )
                .limit(STREAK_LIST_LIMIT)
            )
        except PyMongoError:
            logger.exception("Failed to load streak leaderboard")
            await ctx.send(
                "Không đọc được bảng xếp hạng chuỗi lúc này.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if not top:
            await ctx.send(
                "Chưa có chuỗi đang sống trong server. "
                "Hãy mention, trả lời, hoặc ngồi voice chung.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines: list[str] = []
        for index, document in enumerate(top, start=1):
            medal = medals.get(index, f"`#{index}`")
            current = displayed_current_streak(document, today)
            lines.append(
                f"{medal} <@{document['user_a']}> × <@{document['user_b']}> — "
                f"**{current}** ngày"
            )

        embed = discord.Embed(
            title="🔥 BXH chuỗi tương tác",
            description="\n".join(lines),
            color=STREAK_EMBED_COLOR,
        )
        embed.set_footer(
            text=f"{ctx.guild.name} · Top {len(top)} chuỗi đang sống · UTC+7"
        )
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    async def _send_my_streaks(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        today = self._vietnam_today()
        query = live_streak_query(ctx.guild.id, today)
        query["partner_ids"] = ctx.author.id
        try:
            total = self.collection.count_documents(query)
            documents = list(
                self.collection.find(query)
                .sort(
                    [
                        ("current_streak", DESCENDING),
                        ("longest_streak", DESCENDING),
                    ]
                )
                .limit(STREAK_LIST_LIMIT)
            )
        except PyMongoError:
            logger.exception("Failed to load member interaction streaks")
            await ctx.send(
                "Không đọc được chuỗi tương tác lúc này.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if not documents:
            await ctx.send(
                "Bạn chưa có chuỗi đang sống. Hãy mention, trả lời tin nhắn, "
                "hoặc ngồi voice chung với ai đó.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        lines: list[str] = []
        for document in documents:
            partner_id = other_user_id(document, ctx.author.id)
            current = displayed_current_streak(document, today)
            longest = int(document.get("longest_streak") or current)
            lines.append(
                f"🔥 <@{partner_id}> — **{current}** ngày "
                f"(kỷ lục **{longest}**)"
            )

        embed = discord.Embed(
            title="🔥 Chuỗi tương tác của bạn",
            description="\n".join(lines),
            color=STREAK_EMBED_COLOR,
        )
        extra = ""
        if total > len(documents):
            extra = f" · hiển thị {len(documents)}/{total}"
        embed.set_footer(
            text=(
                f"{total} chuỗi đang sống{extra} · UTC+7 · "
                "Mention, trả lời, hoặc 5 phút voice chung"
            )
        )
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    async def _send_pair_streak(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        assert ctx.guild is not None
        if member.bot:
            await ctx.send(
                "Không thể xem chuỗi với bot.",
                allowed_mentions=NO_MENTIONS,
            )
            return
        if member.id == ctx.author.id:
            await ctx.send(
                "Hãy tag một member khác, hoặc dùng `streak` để xem chuỗi của bạn.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            user_a, user_b = normalize_pair(ctx.author.id, member.id)
        except ValueError:
            await ctx.send(
                "Hãy tag một member khác, hoặc dùng `streak` để xem chuỗi của bạn.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        try:
            document = self.collection.find_one(
                {
                    "guild_id": ctx.guild.id,
                    "user_a": user_a,
                    "user_b": user_b,
                }
            )
        except PyMongoError:
            logger.exception("Failed to load pair interaction streak")
            await ctx.send(
                "Không đọc được chuỗi tương tác lúc này.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        if document is None:
            await ctx.send(
                "Chưa có chuỗi với người này — hãy mention, trả lời, "
                "hoặc ngồi voice chung.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        today = self._vietnam_today()
        current = displayed_current_streak(document, today)
        longest = int(document.get("longest_streak") or 0)
        last_date = document.get("last_active_date") or "—"
        last_source = source_label(document.get("last_source"))
        broken = current == 0

        embed = discord.Embed(
            title="🔥 Chuỗi tương tác",
            description=f"{ctx.author.mention} × {member.mention}",
            color=STREAK_EMBED_COLOR,
        )
        embed.add_field(
            name="Hiện tại",
            value="**0** (đã đứt)" if broken else f"**{current}** ngày",
            inline=True,
        )
        embed.add_field(name="Kỷ lục", value=f"**{longest}** ngày", inline=True)
        embed.add_field(
            name="Lần gần nhất",
            value=f"{last_date} · {last_source}",
            inline=False,
        )
        if broken:
            embed.set_footer(
                text="Tương tác lại hôm nay để bắt đầu chuỗi mới · UTC+7"
            )
        else:
            embed.set_footer(
                text="Mention, trả lời, hoặc 5 phút voice chung · UTC+7"
            )
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InteractStreakCog(bot))
