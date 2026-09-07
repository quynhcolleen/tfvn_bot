"""Post Discord-chat highlights when a SFW message reaches the skull threshold."""

from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
import logging

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.utils._highlight_card import (
    format_highlight_timestamp,
    normalize_highlight_text,
    render_highlight_card,
)
from cogs.utils._highlight_helpers import (
    ACTIVE_STATUSES,
    HIGHLIGHT_CHANNEL_VARIABLE,
    HIGHLIGHT_COLLECTION,
    HIGHLIGHT_MIN_INTERVAL_SECONDS,
    HIGHLIGHT_THRESHOLD,
    MAX_ATTACHMENT_BYTES,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_POSTED,
    STATUS_POSTING,
    HighlightConfigError,
    HighlightLookupError,
    channel_is_nsfw,
    collect_skull_voter_ids,
    count_unique_skull_voters,
    first_image_attachment,
    format_highlight_caption,
    format_highlight_congrats,
    has_renderable_content,
    ignore_reaction_user,
    is_skull_emoji,
    parse_highlight_channel_id,
    seconds_until_highlight_slot,
    should_post_highlight,
)


logger = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
CONGRATS_MENTIONS = discord.AllowedMentions(
    everyone=False,
    users=True,
    roles=False,
    replied_user=True,
)
TERMINAL_STATUSES = (STATUS_POSTED, STATUS_FAILED)


class HighlightCog(commands.Cog):
    """Background 💀 listener that posts chat-theme highlights."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._flush_tasks: dict[int, asyncio.Task] = {}
        self._restored = False
        self._ensure_indexes()

    @property
    def collection(self):
        return self.db[HIGHLIGHT_COLLECTION]

    def _ensure_indexes(self) -> None:
        try:
            self.collection.create_index(
                [("guild_id", ASCENDING), ("source_message_id", ASCENDING)],
                unique=True,
                name="guild_source_unique",
            )
            self.collection.create_index(
                [("status", ASCENDING), ("guild_id", ASCENDING)],
                name="status_guild",
            )
            self.collection.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("status", ASCENDING),
                    ("posted_at", DESCENDING),
                ],
                name="guild_status_posted_at",
            )
            self.collection.create_index(
                [
                    ("guild_id", ASCENDING),
                    ("status", ASCENDING),
                    ("qualified_at", ASCENDING),
                ],
                name="guild_status_qualified_at",
            )
        except Exception:
            logger.exception("Failed to ensure highlight nomination indexes")

    def cog_unload(self) -> None:
        for task in self._flush_tasks.values():
            task.cancel()
        self._flush_tasks.clear()
        self._guild_locks.clear()

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            await self._restore_pending_nominations()

    def _global_var(self, name: str) -> object:
        return getattr(self.bot, "global_vars", {}).get(name)

    def _highlight_channel_id(self) -> int:
        return parse_highlight_channel_id(
            self._global_var(HIGHLIGHT_CHANNEL_VARIABLE)
        )

    def _guild_lock(self, guild_id: int) -> asyncio.Lock:
        return self._guild_locks.setdefault(int(guild_id), asyncio.Lock())

    @staticmethod
    def _server_avatar(author: discord.abc.User) -> discord.Asset:
        guild_avatar = getattr(author, "guild_avatar", None)
        return guild_avatar or author.display_avatar

    @staticmethod
    def _accent_color(author: discord.abc.User) -> tuple[int, int, int] | None:
        if isinstance(author, discord.Member) and author.color.value:
            return author.color.to_rgb()
        return None

    @staticmethod
    def _can_fetch_messages(channel: object) -> bool:
        return callable(getattr(channel, "fetch_message", None))

    @staticmethod
    def _can_send_messages(channel: object) -> bool:
        return callable(getattr(channel, "send", None))

    async def _avatar_bytes(self, author: discord.abc.User) -> bytes | None:
        try:
            return await self._server_avatar(author).with_size(256).read()
        except (discord.DiscordException, OSError):
            logger.warning(
                "Could not download avatar for highlight author %s",
                getattr(author, "id", "unknown"),
                exc_info=True,
            )
            return None

    async def _attachment_bytes(
        self,
        attachment: discord.Attachment | None,
    ) -> bytes | None:
        if attachment is None:
            return None
        size = getattr(attachment, "size", 0) or 0
        if size > MAX_ATTACHMENT_BYTES:
            return None
        try:
            return await attachment.read()
        except (discord.DiscordException, OSError):
            logger.warning(
                "Could not download highlight attachment %s",
                getattr(attachment, "id", "unknown"),
                exc_info=True,
            )
            return None

    async def _get_channel(
        self,
        guild: discord.Guild | None,
        channel_id: int,
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        if guild is not None:
            channel = guild.get_channel(channel_id)
            if channel is not None:
                return channel
            getter = getattr(guild, "get_thread", None)
            if callable(getter):
                thread = getter(channel_id)
                if thread is not None:
                    return thread
        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        if isinstance(fetched, (discord.abc.GuildChannel, discord.Thread)):
            return fetched
        return None

    async def _fetch_message(
        self,
        channel: discord.abc.Messageable,
        message_id: int,
    ) -> discord.Message:
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound as exc:
            raise HighlightLookupError(
                "Không tìm thấy tin nhắn đó."
            ) from exc
        except discord.Forbidden as exc:
            raise HighlightLookupError(
                "Bot không có quyền đọc lịch sử tin nhắn trong kênh đó."
            ) from exc
        except discord.HTTPException as exc:
            logger.exception("Failed to fetch message for highlight")
            raise HighlightLookupError(
                "Không thể tải tin nhắn lúc này. Hãy thử lại sau."
            ) from exc

    def _source_text(self, message: discord.Message) -> str:
        return getattr(message, "clean_content", None) or message.content or ""

    def _validate_source(
        self,
        message: discord.Message,
        highlight_channel: object,
    ) -> tuple[str, object | None]:
        if message.channel.id == getattr(highlight_channel, "id", None):
            raise HighlightLookupError(
                "Không thể highlight tin nhắn trong kênh highlight."
            )
        image = first_image_attachment(list(message.attachments))
        raw_text = self._source_text(message)
        try:
            text = normalize_highlight_text(
                raw_text,
                allow_empty=image is not None,
            )
        except ValueError as exc:
            raise HighlightLookupError(str(exc)) from exc
        if not has_renderable_content(text=text, has_image=image is not None):
            raise HighlightLookupError(
                "Tin nhắn không có chữ hoặc ảnh để tạo highlight."
            )
        if channel_is_nsfw(message.channel):
            raise HighlightLookupError(
                "Không highlight tin nhắn từ kênh NSFW."
            )
        return text, image

    async def _require_highlight_channel(
        self,
        guild: discord.Guild,
    ) -> discord.abc.GuildChannel | discord.Thread:
        try:
            channel_id = self._highlight_channel_id()
        except HighlightConfigError as exc:
            raise HighlightLookupError(
                "Kênh highlight chưa được cấu hình. Quản trị viên hãy dùng "
                f"`setting set_variable {HIGHLIGHT_CHANNEL_VARIABLE}`."
            ) from exc
        channel = await self._get_channel(guild, channel_id)
        if channel is None or not self._can_send_messages(channel):
            raise HighlightLookupError(
                "Không tìm thấy kênh highlight đã cấu hình."
            )
        return channel

    def _last_posted_at(self, guild_id: int) -> datetime | None:
        document = self.collection.find_one(
            {
                "guild_id": int(guild_id),
                "status": STATUS_POSTED,
                "posted_at": {"$ne": None},
            },
            sort=[("posted_at", DESCENDING)],
        )
        if not document:
            return None
        posted_at = document.get("posted_at")
        return posted_at if isinstance(posted_at, datetime) else None

    def _wait_seconds(self, guild_id: int) -> float:
        return seconds_until_highlight_slot(
            self._last_posted_at(guild_id),
            now=discord.utils.utcnow(),
        )

    def _pending_docs(
        self,
        guild_id: int,
        excluding: set[object] | None = None,
    ) -> list[dict]:
        excluded = excluding or set()
        documents = [
            document
            for document in self.collection.find(
                {"guild_id": int(guild_id), "status": STATUS_PENDING}
            )
            if document.get("_id") not in excluded
        ]

        def sort_key(document: dict) -> tuple:
            qualified = document.get("qualified_at") or document.get("created_at")
            return (qualified is None, qualified, str(document.get("_id")))

        documents.sort(key=sort_key)
        return documents

    def _arm_flush(self, guild_id: int, delay: float) -> None:
        guild_id = int(guild_id)
        existing = self._flush_tasks.get(guild_id)
        running = asyncio.current_task()
        if (
            existing is not None
            and not existing.done()
            and existing is not running
        ):
            return

        async def runner() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._flush_guild(guild_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Highlight flush failed for guild %s",
                    guild_id,
                )
            finally:
                if self._flush_tasks.get(guild_id) is asyncio.current_task():
                    self._flush_tasks.pop(guild_id, None)

        self._flush_tasks[guild_id] = asyncio.create_task(runner())

    async def _publish_highlight(self, nomination: dict) -> None:
        guild = self.bot.get_guild(nomination["guild_id"])
        if guild is None:
            raise RuntimeError("Highlight guild is unavailable")

        highlight_channel = await self._require_highlight_channel(guild)
        source_channel = await self._get_channel(
            guild,
            int(nomination["source_channel_id"]),
        )
        if source_channel is None or not self._can_fetch_messages(source_channel):
            raise HighlightLookupError("Không tìm thấy kênh của tin nhắn đó.")

        try:
            message = await self._fetch_message(
                source_channel,
                int(nomination["source_message_id"]),
            )
        except HighlightLookupError:
            self.collection.update_one(
                {"_id": nomination["_id"], "status": STATUS_POSTING},
                {"$set": {"status": STATUS_FAILED}},
            )
            raise

        try:
            text, image = self._validate_source(message, highlight_channel)
        except HighlightLookupError:
            self.collection.update_one(
                {"_id": nomination["_id"], "status": STATUS_POSTING},
                {"$set": {"status": STATUS_FAILED}},
            )
            raise

        author = message.author
        display_name = getattr(author, "display_name", author.name)
        channel_name = getattr(message.channel, "name", "channel")
        timestamp_label = format_highlight_timestamp(message.created_at)
        avatar_bytes = await self._avatar_bytes(author)
        attachment_bytes = await self._attachment_bytes(image)
        if image is not None and attachment_bytes is None and not text:
            self.collection.update_one(
                {"_id": nomination["_id"], "status": STATUS_POSTING},
                {"$set": {"status": STATUS_FAILED}},
            )
            raise HighlightLookupError(
                "Tin nhắn không có chữ hoặc ảnh để tạo highlight."
            )

        card_bytes = await asyncio.to_thread(
            render_highlight_card,
            avatar_bytes=avatar_bytes,
            display_name=display_name,
            channel_name=channel_name,
            message_text=text,
            timestamp_label=timestamp_label,
            accent_rgb=self._accent_color(author),
            attachment_bytes=attachment_bytes,
        )
        jump_url = nomination.get("source_jump_url") or message.jump_url
        filename = f"highlight-{message.id}.png"
        sent = await highlight_channel.send(
            format_highlight_caption(jump_url),
            file=discord.File(BytesIO(card_bytes), filename=filename),
            allowed_mentions=NO_MENTIONS,
        )
        posted_at = discord.utils.utcnow()
        self.collection.update_one(
            {"_id": nomination["_id"], "status": STATUS_POSTING},
            {
                "$set": {
                    "status": STATUS_POSTED,
                    "highlight_message_id": sent.id,
                    "posted_at": posted_at,
                }
            },
        )
        nomination["status"] = STATUS_POSTED
        nomination["highlight_message_id"] = sent.id
        nomination["posted_at"] = posted_at
        await self._send_highlight_congrats(message, sent)

    async def _send_highlight_congrats(
        self,
        message: discord.Message,
        highlight_message: discord.Message,
    ) -> None:
        """Reply on the source message; never fail the TV post for this."""
        author = message.author
        bot_user_id = getattr(self.bot.user, "id", None)
        if bot_user_id is not None and getattr(author, "id", None) == bot_user_id:
            return
        reply = getattr(message, "reply", None)
        if not callable(reply):
            return
        mention = getattr(author, "mention", None) or f"<@{getattr(author, 'id', 0)}>"
        jump_url = getattr(highlight_message, "jump_url", None) or None
        try:
            await reply(
                format_highlight_congrats(mention, jump_url),
                mention_author=True,
                allowed_mentions=CONGRATS_MENTIONS,
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.warning(
                "Could not reply with highlight congrats on message %s",
                getattr(message, "id", "unknown"),
                exc_info=True,
            )

    async def _flush_guild(self, guild_id: int) -> None:
        async with self._guild_lock(guild_id):
            skipped: set[object] = set()
            while True:
                wait = self._wait_seconds(guild_id)
                if wait > 0:
                    self._arm_flush(guild_id, wait)
                    return
                pending = self._pending_docs(guild_id, excluding=skipped)
                if not pending:
                    return
                nomination = pending[0]
                synced = await self._sync_voters(nomination)
                unique_count = count_unique_skull_voters(
                    synced.get("voter_ids") or (),
                    bot_user_id=getattr(self.bot.user, "id", None),
                )
                if not should_post_highlight(
                    synced.get("status", STATUS_PENDING),
                    unique_count,
                ):
                    skipped.add(synced.get("_id"))
                    continue
                claimed = self.collection.find_one_and_update(
                    {"_id": synced["_id"], "status": STATUS_PENDING},
                    {"$set": {"status": STATUS_POSTING}},
                    return_document=ReturnDocument.AFTER,
                )
                if claimed is None:
                    skipped.add(synced.get("_id"))
                    continue
                posted = False
                try:
                    await self._publish_highlight(claimed)
                    posted = True
                except HighlightLookupError:
                    still_posting = self.collection.find_one(
                        {"_id": claimed["_id"], "status": STATUS_POSTING}
                    )
                    if still_posting is not None:
                        self.collection.update_one(
                            {"_id": claimed["_id"], "status": STATUS_POSTING},
                            {"$set": {"status": STATUS_PENDING}},
                        )
                except Exception:
                    logger.exception(
                        "Failed to publish highlight for source message %s",
                        claimed.get("source_message_id"),
                    )
                    self.collection.update_one(
                        {"_id": claimed["_id"], "status": STATUS_POSTING},
                        {"$set": {"status": STATUS_PENDING}},
                    )
                if not posted:
                    skipped.add(claimed.get("_id"))
                    continue
                wait = self._wait_seconds(guild_id)
                remaining = self._pending_docs(guild_id)
                if remaining:
                    self._arm_flush(
                        guild_id,
                        wait if wait > 0 else float(HIGHLIGHT_MIN_INTERVAL_SECONDS),
                    )
                return

    async def _sync_voters(self, nomination: dict) -> dict:
        guild = self.bot.get_guild(nomination["guild_id"])
        channel = await self._get_channel(
            guild,
            int(nomination["source_channel_id"]),
        )
        if channel is None or not self._can_fetch_messages(channel):
            return nomination
        try:
            message = await channel.fetch_message(
                int(nomination["source_message_id"])
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            if nomination.get("status") != STATUS_POSTED:
                self.collection.update_one(
                    {"_id": nomination["_id"]},
                    {"$set": {"status": STATUS_FAILED}},
                )
                nomination["status"] = STATUS_FAILED
            return nomination

        voters = await collect_skull_voter_ids(
            message,
            bot_user_id=getattr(self.bot.user, "id", None),
        )
        updated = self.collection.find_one_and_update(
            {"_id": nomination["_id"]},
            {"$set": {"voter_ids": voters}},
            return_document=ReturnDocument.AFTER,
        )
        return updated or {**nomination, "voter_ids": voters}

    def _upsert_qualified(
        self,
        message: discord.Message,
        voters: list[int],
        *,
        now: datetime,
    ) -> dict | None:
        guild_id = message.guild.id if message.guild is not None else None
        if guild_id is None:
            return None
        existing = self.collection.find_one(
            {
                "guild_id": int(guild_id),
                "source_message_id": int(message.id),
            }
        )
        if existing is not None and existing.get("status") in TERMINAL_STATUSES:
            return None
        if existing is not None and existing.get("status") == STATUS_POSTING:
            return None

        fields = {
            "voter_ids": voters,
            "source_channel_id": message.channel.id,
            "source_jump_url": message.jump_url,
            "source_author_id": message.author.id,
            "nsfw": channel_is_nsfw(message.channel),
        }
        if existing is None:
            document = {
                "guild_id": int(guild_id),
                "source_message_id": int(message.id),
                "status": STATUS_PENDING,
                "highlight_message_id": None,
                "created_at": now,
                "qualified_at": now,
                "posted_at": None,
                **fields,
            }
            try:
                inserted = self.collection.insert_one(document)
            except DuplicateKeyError:
                existing = self.collection.find_one(
                    {
                        "guild_id": int(guild_id),
                        "source_message_id": int(message.id),
                    }
                )
                if existing is None or existing.get("status") in TERMINAL_STATUSES:
                    return None
            else:
                document["_id"] = inserted.inserted_id
                return document

        update = {"$set": fields}
        if existing is not None and not existing.get("qualified_at"):
            update["$set"]["qualified_at"] = now
        updated = self.collection.find_one_and_update(
            {
                "guild_id": int(guild_id),
                "source_message_id": int(message.id),
                "status": STATUS_PENDING,
            },
            update,
            return_document=ReturnDocument.AFTER,
        )
        return updated

    async def _message_from_payload(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> discord.Message | None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        channel = await self._get_channel(guild, payload.channel_id)
        if channel is None or not self._can_fetch_messages(channel):
            return None
        try:
            return await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _is_highlight_channel_message(self, message: discord.Message) -> bool:
        try:
            highlight_id = self._highlight_channel_id()
        except HighlightConfigError:
            return False
        return message.channel.id == highlight_id

    async def _payload_source_channel(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> discord.abc.GuildChannel | discord.Thread | None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        return await self._get_channel(guild, payload.channel_id)

    async def _restore_pending_nominations(self) -> None:
        if self._restored:
            return
        self._restored = True
        try:
            pending = list(
                self.collection.find({"status": {"$in": list(ACTIVE_STATUSES)}})
            )
        except PyMongoError:
            logger.exception("Failed to load pending highlight nominations")
            self._restored = False
            return

        guild_ids: set[int] = set()
        for nomination in pending:
            if nomination.get("status") == STATUS_POSTING:
                if nomination.get("highlight_message_id"):
                    self.collection.update_one(
                        {"_id": nomination["_id"], "status": STATUS_POSTING},
                        {"$set": {"status": STATUS_POSTED}},
                    )
                    continue
                self.collection.update_one(
                    {"_id": nomination["_id"], "status": STATUS_POSTING},
                    {"$set": {"status": STATUS_PENDING}},
                )
                nomination["status"] = STATUS_PENDING
            synced = await self._sync_voters(nomination)
            unique_count = count_unique_skull_voters(
                synced.get("voter_ids") or (),
                bot_user_id=getattr(self.bot.user, "id", None),
            )
            if should_post_highlight(
                synced.get("status", STATUS_PENDING),
                unique_count,
            ):
                guild_ids.add(int(synced["guild_id"]))

        for guild_id in guild_ids:
            self._arm_flush(guild_id, 0)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._restore_pending_nominations()

    async def _handle_skull_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        message = await self._message_from_payload(payload)
        if message is None or message.guild is None:
            return
        if channel_is_nsfw(message.channel):
            return
        if self._is_highlight_channel_message(message):
            return

        bot_user_id = getattr(self.bot.user, "id", None)
        voters = await collect_skull_voter_ids(message, bot_user_id=bot_user_id)
        unique_count = count_unique_skull_voters(
            voters,
            bot_user_id=bot_user_id,
        )
        if unique_count < HIGHLIGHT_THRESHOLD:
            return

        nomination = self._upsert_qualified(
            message,
            voters,
            now=discord.utils.utcnow(),
        )
        if nomination is None:
            return
        self._arm_flush(message.guild.id, 0)

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        if payload.guild_id is None or not is_skull_emoji(payload.emoji):
            return
        if ignore_reaction_user(
            user_id=payload.user_id,
            bot_user_id=getattr(self.bot.user, "id", None),
            member=payload.member,
        ):
            return
        channel = await self._payload_source_channel(payload)
        if channel is not None and channel_is_nsfw(channel):
            return
        await self._handle_skull_add(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        if payload.guild_id is None or not is_skull_emoji(payload.emoji):
            return
        if ignore_reaction_user(
            user_id=payload.user_id,
            bot_user_id=getattr(self.bot.user, "id", None),
            member=payload.member,
        ):
            return
        channel = await self._payload_source_channel(payload)
        if channel is not None and channel_is_nsfw(channel):
            return
        self.collection.update_one(
            {
                "guild_id": int(payload.guild_id),
                "source_message_id": int(payload.message_id),
                "status": STATUS_PENDING,
            },
            {"$pull": {"voter_ids": int(payload.user_id)}},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HighlightCog(bot))
