"""Photo-dump raid filter for fake MrBeast giveaway scams."""

from __future__ import annotations

from datetime import timedelta, timezone
from io import BytesIO
import logging
import secrets
from typing import Any

import discord
from discord.ext import commands, tasks
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from cogs.mod._case_helpers import (
    can_moderate,
    format_audit_reason,
    record_case,
)
from cogs.mod._mrbeast_scam_helpers import (
    ACTION_CHALLENGE,
    ACTION_TIMEOUT,
    CHALLENGE_SECONDS,
    IMMEDIATE_TIMEOUT_DUMP_COUNT,
    MAX_ALERT_FILES,
    MAX_ATTACHMENT_BYTES,
    SOURCE_PHOTO_DUMP,
    SOURCE_TEXT,
    TIMEOUT_HOURS,
    WARNING_DUMP_COUNT,
    TextScore,
    WindowHit,
    collect_text_corpus,
    count_image_attachments,
    is_image_attachment,
    is_photo_dump_candidate,
    parent_channel_id,
    photo_dump_action,
    photo_dump_count,
    related_photo_hits,
    score_text,
    should_escalate_text,
    timeout_until,
    truncate_snapshot,
    upsert_window,
)
from cogs.mod._reply_target import ReplyTargetError, fetch_same_channel_reply
from cogs.operation._setup_helpers import parse_discord_id


logger = logging.getLogger(__name__)

LOGS_COLLECTION = "mrbeast_scam_logs"
INCIDENTS_COLLECTION = "mrbeast_scam_incidents"
ALERT_CHANNEL_VARIABLE = "MRBEAST_SCAM_ALERT_CHANNEL"
SCAM_CHECK_COOLDOWN_SECONDS = 5
BAN_BUTTON_CUSTOM_ID = "tfvn:mrbeast:ban"
UNTIMEOUT_BUTTON_CUSTOM_ID = "tfvn:mrbeast:untimeout"
KEEP_BUTTON_CUSTOM_ID = "tfvn:mrbeast:keep"
SCAM_REASON = "Giả mạo giveaway MrBeast (đăng ảnh nhiều lần)"


def _as_aware(value: Any) -> Any:
    if value is None:
        return None
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _is_staff_member(author: object) -> bool:
    permissions = getattr(author, "guild_permissions", None)
    if permissions is None:
        return False
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_messages", False)
    )


def _author_names(author: object) -> tuple[str, ...]:
    names: list[str] = []
    for attr in ("display_name", "global_name", "name"):
        value = getattr(author, attr, None)
        if isinstance(value, str) and value.strip() and value not in names:
            names.append(value)
    return tuple(names)


def _embed_texts(message: discord.Message) -> tuple[str, ...]:
    texts: list[str] = []
    for embed in message.embeds[:10]:
        for value in (
            embed.title,
            embed.description,
            getattr(embed.author, "name", None),
            getattr(embed.footer, "text", None),
            embed.url,
        ):
            if isinstance(value, str) and value.strip():
                texts.append(value)
        for field in embed.fields[:25]:
            if field.name:
                texts.append(str(field.name))
            if field.value:
                texts.append(str(field.value))
        if embed.image and embed.image.url:
            texts.append(embed.image.url)
    return tuple(texts)


def _sticker_names(message: discord.Message) -> tuple[str, ...]:
    names: list[str] = []
    for sticker in getattr(message, "stickers", ()) or ():
        name = getattr(sticker, "name", None)
        if isinstance(name, str) and name.strip():
            names.append(name)
    return tuple(names)


class MrBeastWarningView(discord.ui.View):
    """Author must click within 30s or they are timed out."""

    def __init__(self, author_id: int, guild_id: int) -> None:
        super().__init__(timeout=CHALLENGE_SECONDS)
        self.author_id = author_id
        self.guild_id = guild_id
        self.confirmed = False
        self.superseded = False
        self.message: discord.Message | None = None

    def _disable_buttons(self) -> None:
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(
                "Cảnh báo này chỉ dùng được trong server đang xử lý.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Chỉ người bị cảnh báo mới bấm được nút này.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        return True

    @discord.ui.button(label="Xác nhận", style=discord.ButtonStyle.success)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.confirmed = True
        self._disable_buttons()
        await interaction.response.edit_message(
            content=(
                f"{interaction.user.mention} đã xác nhận trong 30 giây. "
                "Chưa timeout. Dump ảnh lần 5 trong 2 phút sẽ timeout ngay."
            ),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.stop()

    async def on_timeout(self) -> None:
        self._disable_buttons()
        if self.message is None or self.superseded or self.confirmed:
            return
        try:
            await self.message.edit(
                content=(
                    "Không bấm xác nhận trong 30 giây. "
                    "Đang timeout 24 giờ."
                ),
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return


class MrBeastScamDecisionView(discord.ui.View):
    def __init__(self, cog: "MrBeastScamCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Bảng này chỉ dùng được trong server.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions is None or not (
            permissions.ban_members or permissions.manage_messages
        ):
            await interaction.response.send_message(
                "Cần quyền Ban Members hoặc Manage Messages để dùng bảng này.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return False
        return True

    @discord.ui.button(
        label="Ban",
        style=discord.ButtonStyle.danger,
        custom_id=BAN_BUTTON_CUSTOM_ID,
    )
    async def ban_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.handle_decision(interaction, "banned")

    @discord.ui.button(
        label="Gỡ timeout",
        style=discord.ButtonStyle.success,
        custom_id=UNTIMEOUT_BUTTON_CUSTOM_ID,
    )
    async def untimeout_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.handle_decision(interaction, "untimeout")

    @discord.ui.button(
        label="Giữ timeout",
        style=discord.ButtonStyle.secondary,
        custom_id=KEEP_BUTTON_CUSTOM_ID,
    )
    async def keep_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self.cog.handle_decision(interaction, "kept")


class MrBeastScamCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.logs = self.db[LOGS_COLLECTION]
        self.incidents = self.db[INCIDENTS_COLLECTION]
        self._window: list[WindowHit] = []
        self._active_targets: set[tuple[int, int]] = set()
        self._pending_challenges: dict[tuple[int, int], MrBeastWarningView] = {}
        self._ensure_indexes()
        self.expire_incidents.start()

    def cog_unload(self) -> None:
        self.expire_incidents.cancel()

    def _ensure_indexes(self) -> None:
        try:
            self.logs.create_index(
                [("guild_id", ASCENDING), ("created_at", ASCENDING)],
                name="guild_created",
            )
            self.incidents.create_index(
                [("guild_id", ASCENDING), ("incident_id", ASCENDING)],
                unique=True,
                name="guild_incident_unique",
            )
            self.incidents.create_index(
                [("guild_id", ASCENDING), ("target_id", ASCENDING), ("status", ASCENDING)],
                name="guild_target_status",
            )
        except PyMongoError:
            logger.exception("Failed to create MrBeast scam indexes")

    def _should_scan(self, message: discord.Message) -> bool:
        if message.guild is None:
            return False
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return False
        if message.webhook_id is None and getattr(message.author, "bot", False):
            return False
        if message.webhook_id is None and _is_staff_member(message.author):
            return False
        return True

    def _score_message(self, message: discord.Message) -> TextScore:
        names = _author_names(message.author)
        corpus = collect_text_corpus(
            message.content or "",
            author_names=names,
            embed_texts=_embed_texts(message),
            attachment_names=tuple(
                str(getattr(attachment, "filename", ""))
                for attachment in message.attachments
            ),
            sticker_names=_sticker_names(message),
        )
        return score_text(
            corpus,
            author_names=names,
            mention_everyone=bool(getattr(message, "mention_everyone", False)),
        )

    def _window_hit(
        self,
        message: discord.Message,
        *,
        source: str,
        image_count: int,
        fingerprint: str,
        now: Any,
    ) -> WindowHit:
        return WindowHit(
            guild_id=message.guild.id,
            author_id=message.author.id,
            parent_channel_id=parent_channel_id(message.channel),
            channel_id=message.channel.id,
            message_id=message.id,
            source=source,
            image_count=image_count,
            fingerprint=fingerprint,
            created_at=now,
        )

    async def _handle_message(self, message: discord.Message) -> None:
        if not self._should_scan(message):
            return

        now = discord.utils.utcnow()
        image_count, other_count = count_image_attachments(message.attachments)
        dump = is_photo_dump_candidate(
            image_count,
            message.content or "",
            other_count,
        )
        text_score = self._score_message(message)

        if dump:
            hit = self._window_hit(
                message,
                source=SOURCE_PHOTO_DUMP,
                image_count=image_count,
                fingerprint="",
                now=now,
            )
            self._window = upsert_window(self._window, hit, now)
            action = photo_dump_action(self._window, hit)
            if action == ACTION_TIMEOUT:
                await self._timeout_now(message, hit, text_score)
            elif action == ACTION_CHALLENGE:
                await self._challenge(message, hit, text_score)
            return

        if not text_score.hit:
            return

        hit = self._window_hit(
            message,
            source=SOURCE_TEXT,
            image_count=image_count,
            fingerprint=text_score.fingerprint,
            now=now,
        )
        self._window = upsert_window(self._window, hit, now)
        await self._delete_message(message)
        self._write_log(message, text_score, hit, escalated=False, source="text")
        if should_escalate_text(self._window, hit):
            await self._timeout_now(message, hit, text_score)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._handle_message(message)

    @commands.Cog.listener()
    async def on_message_edit(
        self,
        before: discord.Message,
        after: discord.Message,
    ) -> None:
        before_ids = tuple(getattr(item, "id", None) for item in before.attachments)
        after_ids = tuple(getattr(item, "id", None) for item in after.attachments)
        if before.content == after.content and before_ids == after_ids:
            return
        await self._handle_message(after)

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        return guild.me if guild.me is not None else (
            guild.get_member(self.bot.user.id) if self.bot.user else None
        )

    def _can_timeout(self, member: discord.Member) -> bool:
        guild = member.guild
        bot_member = self._bot_member(guild)
        if bot_member is None or not bot_member.guild_permissions.moderate_members:
            return False
        if member.id == guild.owner_id or member.id == bot_member.id:
            return False
        if bot_member.id != guild.owner_id and member.top_role >= bot_member.top_role:
            return False
        return True

    def _can_delete(self, message: discord.Message) -> bool:
        guild = message.guild
        if guild is None:
            return False
        bot_member = self._bot_member(guild)
        if bot_member is None:
            return False
        channel = message.channel
        permissions = getattr(channel, "permissions_for", None)
        if not callable(permissions):
            return bool(bot_member.guild_permissions.manage_messages)
        return bool(permissions(bot_member).manage_messages)

    async def _delete_message(self, message: discord.Message) -> bool:
        if not self._can_delete(message):
            return False
        try:
            await message.delete()
            return True
        except (discord.Forbidden, discord.NotFound):
            return False
        except discord.HTTPException:
            logger.exception(
                "Failed deleting scam message guild=%s message=%s",
                getattr(message.guild, "id", None),
                message.id,
            )
            return False

    async def _delete_window_messages(
        self,
        guild: discord.Guild,
        hits: tuple[WindowHit, ...],
    ) -> None:
        for hit in hits:
            channel = guild.get_channel_or_thread(hit.channel_id)
            if channel is None:
                continue
            try:
                await channel.get_partial_message(hit.message_id).delete()
            except (discord.Forbidden, discord.NotFound):
                continue
            except discord.HTTPException:
                logger.exception(
                    "Failed deleting window scam message guild=%s message=%s",
                    guild.id,
                    hit.message_id,
                )

    async def _load_message(
        self,
        guild: discord.Guild,
        channel_id: int,
        message_id: int,
    ) -> discord.Message | None:
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

    async def _evidence_files(
        self,
        messages: list[discord.Message],
    ) -> list[discord.File]:
        files: list[discord.File] = []
        seen_names: set[str] = set()
        for message in messages:
            for attachment in message.attachments:
                if len(files) >= MAX_ALERT_FILES:
                    return files
                if not is_image_attachment(attachment):
                    continue
                size = getattr(attachment, "size", 0) or 0
                if size > MAX_ATTACHMENT_BYTES:
                    continue
                try:
                    payload = await attachment.read()
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    continue
                if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
                    continue
                name = str(attachment.filename or "image.png")
                if name in seen_names:
                    name = f"{message.id}_{name}"
                seen_names.add(name)
                files.append(discord.File(BytesIO(payload), filename=name))
        return files

    def _alert_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        variables = getattr(self.bot, "global_vars", None) or {}
        channel_id = parse_discord_id(variables.get(ALERT_CHANNEL_VARIABLE))
        channel = self._resolve_guild_channel(guild, channel_id)
        if channel is not None:
            return channel
        try:
            config = self.db["moderation_config"].find_one({"guild_id": guild.id}) or {}
        except PyMongoError:
            logger.exception("Failed reading case log channel guild=%s", guild.id)
            return None
        return self._resolve_guild_channel(
            guild,
            parse_discord_id(config.get("log_channel_id")),
        )

    def _resolve_guild_channel(
        self,
        guild: discord.Guild,
        channel_id: int | None,
    ) -> discord.abc.Messageable | None:
        if channel_id is None:
            return None
        getter = getattr(guild, "get_channel_or_thread", None)
        channel = getter(channel_id) if callable(getter) else guild.get_channel(channel_id)
        if channel is None:
            channel = self.bot.get_channel(channel_id)
        other_guild = getattr(channel, "guild", None)
        if other_guild is not None and other_guild.id != guild.id:
            return None
        sender = getattr(channel, "send", None)
        if not callable(sender):
            return None
        return channel

    def _write_log(
        self,
        message: discord.Message,
        text_score: TextScore,
        hit: WindowHit,
        *,
        escalated: bool,
        source: str,
        timeout_applied: bool = False,
    ) -> None:
        try:
            self.logs.insert_one(
                {
                    "guild_id": message.guild.id if message.guild else hit.guild_id,
                    "channel_id": message.channel.id,
                    "message_id": message.id,
                    "author_id": message.author.id,
                    "webhook_id": message.webhook_id,
                    "source": source,
                    "signals": list(text_score.signals),
                    "score": text_score.score,
                    "reason": text_score.reason,
                    "snapshot": truncate_snapshot(message.content or ""),
                    "image_count": hit.image_count,
                    "escalated": escalated,
                    "timeout_applied": timeout_applied,
                    "created_at": discord.utils.utcnow(),
                }
            )
        except PyMongoError:
            logger.exception(
                "Failed writing scam log guild=%s message=%s",
                hit.guild_id,
                message.id,
            )

    def _challenge_key(self, message: discord.Message) -> tuple[int, int]:
        guild = message.guild
        assert guild is not None
        return (guild.id, message.author.id)

    async def _timeout_now(
        self,
        message: discord.Message,
        hit: WindowHit,
        text_score: TextScore,
    ) -> None:
        key = self._challenge_key(message)
        view = self._pending_challenges.get(key)
        if view is not None:
            view.superseded = True
            view.stop()
        await self._escalate(message, hit, text_score)

    async def _challenge(
        self,
        message: discord.Message,
        hit: WindowHit,
        text_score: TextScore,
    ) -> None:
        guild = message.guild
        if guild is None:
            return
        key = self._challenge_key(message)
        if key in self._pending_challenges or key in self._active_targets:
            return

        count = photo_dump_count(self._window, hit)
        view = MrBeastWarningView(message.author.id, guild.id)
        self._pending_challenges[key] = view
        channel = message.channel
        sender = getattr(channel, "send", None)
        prompt = None
        if callable(sender):
            try:
                prompt = await sender(
                    (
                        f"{message.author.mention} Cảnh báo dump ảnh "
                        f"**lần {count}/{IMMEDIATE_TIMEOUT_DUMP_COUNT}** trong 2 phút.\n"
                        f"Bấm **Xác nhận** trong **{CHALLENGE_SECONDS} giây** "
                        "nếu đây không phải spam giveaway. "
                        "Không bấm sẽ bị timeout 24 giờ. "
                        f"Lần {IMMEDIATE_TIMEOUT_DUMP_COUNT} sẽ timeout ngay."
                    ),
                    view=view,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception(
                    "Failed sending dump warning guild=%s user=%s",
                    guild.id,
                    message.author.id,
                )
                prompt = None
        view.message = prompt
        if prompt is None:
            self._pending_challenges.pop(key, None)
            await self._escalate(message, hit, text_score)
            return

        await view.wait()
        self._pending_challenges.pop(key, None)
        if view.superseded:
            return
        if view.confirmed:
            self._write_log(
                message,
                text_score,
                hit,
                escalated=False,
                source="photo_challenge_ok",
            )
            return
        await self._escalate(message, hit, text_score)

    async def _escalate(
        self,
        message: discord.Message,
        hit: WindowHit,
        text_score: TextScore,
    ) -> None:
        guild = message.guild
        if guild is None:
            return
        key = (guild.id, message.author.id)
        if key in self._active_targets:
            await self._delete_message(message)
            return
        self._active_targets.add(key)
        try:
            related = (
                related_photo_hits(self._window, hit)
                if hit.source == SOURCE_PHOTO_DUMP
                else (hit,)
            )
            evidence_messages = [message]
            for row in related:
                if row.message_id == message.id:
                    continue
                loaded = await self._load_message(guild, row.channel_id, row.message_id)
                if loaded is not None:
                    evidence_messages.append(loaded)

            files = await self._evidence_files(evidence_messages)
            await self._delete_message(message)
            await self._delete_window_messages(guild, related)

            existing = self.incidents.find_one(
                {
                    "guild_id": guild.id,
                    "target_id": message.author.id,
                    "status": "pending",
                }
            )
            if existing is not None:
                self._touch_incident(existing, related)
                self._write_log(
                    message,
                    text_score,
                    hit,
                    escalated=True,
                    source=hit.source,
                )
                return

            member = message.author if isinstance(message.author, discord.Member) else None
            if member is None:
                member = guild.get_member(message.author.id)
            timeout_applied = False
            until = timeout_until(discord.utils.utcnow())
            if member is not None and self._can_timeout(member):
                try:
                    await member.timeout(
                        until,
                        reason=format_audit_reason(SCAM_REASON, self._bot_member(guild)),
                    )
                    timeout_applied = True
                except (discord.Forbidden, discord.NotFound):
                    timeout_applied = False
                except discord.HTTPException:
                    logger.exception(
                        "Failed timeout for scam raid guild=%s user=%s",
                        guild.id,
                        message.author.id,
                    )

            bot_member = self._bot_member(guild) or message.author
            await record_case(
                self.bot,
                guild=guild,
                target=message.author,
                moderator=bot_member,
                action="scam",
                reason=SCAM_REASON,
            )
            if timeout_applied:
                await record_case(
                    self.bot,
                    guild=guild,
                    target=message.author,
                    moderator=bot_member,
                    action="timeout",
                    reason=SCAM_REASON,
                    duration_seconds=TIMEOUT_HOURS * 3600,
                )

            self._write_log(
                message,
                text_score,
                hit,
                escalated=True,
                source=hit.source,
                timeout_applied=timeout_applied,
            )
            await self._open_incident(
                guild,
                message,
                hit,
                related,
                text_score,
                timeout_applied=timeout_applied,
                timeout_until_at=until if timeout_applied else None,
                files=files,
            )
        finally:
            self._active_targets.discard(key)

    def _touch_incident(self, incident: dict, hits: tuple[WindowHit, ...]) -> None:
        try:
            self.incidents.update_one(
                {"_id": incident["_id"]},
                {
                    "$addToSet": {
                        "channels": {"$each": [row.parent_channel_id for row in hits]},
                    },
                    "$set": {"updated_at": discord.utils.utcnow()},
                },
            )
        except PyMongoError:
            logger.exception(
                "Failed updating pending scam incident %s",
                incident.get("incident_id"),
            )

    async def _open_incident(
        self,
        guild: discord.Guild,
        message: discord.Message,
        hit: WindowHit,
        related: tuple[WindowHit, ...],
        text_score: TextScore,
        *,
        timeout_applied: bool,
        timeout_until_at: Any,
        files: list[discord.File],
    ) -> None:
        incident_id = secrets.token_hex(8)
        channels = sorted({row.parent_channel_id for row in related})
        document = {
            "incident_id": incident_id,
            "guild_id": guild.id,
            "target_id": message.author.id,
            "target_name": str(message.author),
            "webhook_id": message.webhook_id,
            "status": "pending",
            "source": hit.source,
            "channels": channels,
            "image_count": hit.image_count,
            "signals": list(text_score.signals),
            "reason": text_score.reason,
            "timeout_applied": timeout_applied,
            "timeout_until": timeout_until_at,
            "alert_channel_id": None,
            "alert_message_id": None,
            "created_at": discord.utils.utcnow(),
            "updated_at": discord.utils.utcnow(),
            "resolved_at": None,
            "resolved_by": None,
        }
        channel = self._alert_channel(guild)
        if channel is None:
            logger.warning(
                "No MrBeast scam alert channel guild=%s; timeout_applied=%s",
                guild.id,
                timeout_applied,
            )
            try:
                self.incidents.insert_one(document)
            except PyMongoError:
                logger.exception("Failed storing scam incident without alert channel")
            return

        embed = self._incident_embed(guild, message.author, document, related)
        view = MrBeastScamDecisionView(self)
        try:
            sent = await channel.send(
                embed=embed,
                files=files,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            logger.warning("Missing permission to send scam alert guild=%s", guild.id)
            sent = None
        except discord.HTTPException:
            logger.exception("Failed sending scam alert guild=%s", guild.id)
            sent = None
        if sent is not None:
            document["alert_channel_id"] = sent.channel.id
            document["alert_message_id"] = sent.id
        try:
            self.incidents.insert_one(document)
        except PyMongoError:
            logger.exception("Failed storing scam incident guild=%s", guild.id)

    def _incident_embed(
        self,
        guild: discord.Guild,
        author: discord.abc.User,
        incident: dict,
        related: tuple[WindowHit, ...] | None = None,
    ) -> discord.Embed:
        timeout_note = (
            f"Đã timeout {TIMEOUT_HOURS} giờ. Chọn Ban, gỡ timeout, hoặc giữ timeout."
            if incident.get("timeout_applied")
            else "Không timeout được (webhook, thiếu quyền, hoặc role). Vẫn cần quyết định."
        )
        channels = incident.get("channels") or []
        channel_mentions = ", ".join(f"<#{channel_id}>" for channel_id in channels) or "không rõ"
        created = getattr(author, "created_at", None)
        joined = getattr(author, "joined_at", None)
        embed = discord.Embed(
            title="Cảnh báo giveaway giả mạo MrBeast",
            description=timeout_note,
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Thành viên",
            value=f"{author.mention} (`{author.id}`)\n{incident.get('target_name', author)}",
            inline=False,
        )
        embed.add_field(name="Kênh", value=channel_mentions[:1024], inline=False)
        if created is not None:
            embed.add_field(
                name="Tài khoản tạo",
                value=discord.utils.format_dt(created, style="R"),
                inline=True,
            )
        if joined is not None:
            embed.add_field(
                name="Vào server",
                value=discord.utils.format_dt(joined, style="R"),
                inline=True,
            )
        embed.add_field(
            name="Ảnh / tín hiệu",
            value=(
                f"{incident.get('image_count', 0)} ảnh"
                + (f" · {incident.get('reason')}" if incident.get("reason") else "")
            )[:1024],
            inline=False,
        )
        if related:
            embed.set_footer(
                text=f"ID {incident['incident_id']} · {len(related)} tin trong cửa sổ 2 phút"
            )
        else:
            embed.set_footer(text=f"ID {incident['incident_id']}")
        return embed

    def _disable_view(self) -> MrBeastScamDecisionView:
        view = MrBeastScamDecisionView(self)
        for item in view.children:
            item.disabled = True
        return view

    async def handle_decision(
        self,
        interaction: discord.Interaction,
        status: str,
    ) -> None:
        guild = interaction.guild
        if guild is None or interaction.message is None:
            await interaction.response.send_message(
                "Không tìm thấy bảng quyết định.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        permissions = getattr(interaction.user, "guild_permissions", None)
        if status == "banned":
            if permissions is None or not permissions.ban_members:
                await interaction.response.send_message(
                    "Bạn không còn quyền Ban Members.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
        elif permissions is None or not permissions.manage_messages:
            await interaction.response.send_message(
                "Bạn không còn quyền Manage Messages.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        incident = self.incidents.find_one(
            {
                "guild_id": guild.id,
                "alert_message_id": interaction.message.id,
            }
        )
        if incident is None:
            await interaction.response.send_message(
                "Không tìm thấy sự cố của bảng này.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if incident.get("status") != "pending":
            await interaction.response.send_message(
                "Sự cố này đã được xử lý.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        claimed = self.incidents.find_one_and_update(
            {
                "guild_id": guild.id,
                "incident_id": incident["incident_id"],
                "status": "pending",
            },
            {
                "$set": {
                    "status": status,
                    "resolved_at": discord.utils.utcnow(),
                    "resolved_by": interaction.user.id,
                    "updated_at": discord.utils.utcnow(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            await interaction.response.send_message(
                "Sự cố này vừa được người khác xử lý.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        error = await self._apply_decision(guild, interaction.user, claimed, status)
        if error is not None:
            self.incidents.update_one(
                {"_id": claimed["_id"]},
                {
                    "$set": {
                        "status": "pending",
                        "resolved_at": None,
                        "resolved_by": None,
                        "updated_at": discord.utils.utcnow(),
                    }
                },
            )
            await interaction.response.send_message(
                error,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        labels = {
            "banned": "Đã ban thành viên.",
            "untimeout": "Đã gỡ timeout (báo cáo nhầm).",
            "kept": "Giữ timeout 24 giờ.",
        }
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed is None:
            embed = discord.Embed(title="Cảnh báo giveaway giả mạo MrBeast")
        embed.color = (
            discord.Color.dark_red()
            if status == "banned"
            else discord.Color.green()
            if status == "untimeout"
            else discord.Color.orange()
        )
        embed.add_field(
            name="Quyết định",
            value=f"{labels[status]} bởi {interaction.user.mention}",
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=self._disable_view(),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _apply_decision(
        self,
        guild: discord.Guild,
        moderator: discord.Member | discord.User,
        incident: dict,
        status: str,
    ) -> str | None:
        target_id = int(incident["target_id"])
        bot_member = self._bot_member(guild)
        if bot_member is None:
            return "Bot không còn là thành viên của server."

        member = guild.get_member(target_id)
        if status == "banned":
            if member is not None:
                if not can_moderate(moderator, member) and moderator.id != guild.owner_id:
                    return "Bạn không thể ban thành viên này."
                if member.id == guild.owner_id:
                    return "Không thể ban chủ server."
                if bot_member.id != guild.owner_id and member.top_role >= bot_member.top_role:
                    return "Role bot phải cao hơn thành viên cần ban."
            if not bot_member.guild_permissions.ban_members:
                return "Bot không có quyền Ban Members."
            reason = format_audit_reason(SCAM_REASON, moderator)
            try:
                if member is not None:
                    await member.ban(reason=reason, delete_message_seconds=0)
                else:
                    await guild.ban(
                        discord.Object(id=target_id),
                        reason=reason,
                        delete_message_seconds=0,
                    )
            except discord.Forbidden:
                return "Bot không thể ban thành viên này."
            except discord.HTTPException:
                logger.exception("Failed banning scam target=%s", target_id)
                return "Discord từ chối thao tác ban."
            await record_case(
                self.bot,
                guild=guild,
                target=member or discord.Object(id=target_id),
                moderator=moderator,
                action="ban",
                reason=SCAM_REASON,
            )
            return None

        if member is None:
            if status == "kept":
                return None
            return "Thành viên không còn trong server."

        if status == "untimeout":
            try:
                await member.timeout(
                    None,
                    reason=format_audit_reason("Gỡ timeout: báo cáo nhầm giveaway MrBeast", moderator),
                )
            except discord.Forbidden:
                return "Bot không thể gỡ timeout của thành viên này."
            except discord.HTTPException:
                logger.exception("Failed clearing scam timeout target=%s", target_id)
                return "Discord từ chối gỡ timeout."
            await record_case(
                self.bot,
                guild=guild,
                target=member,
                moderator=moderator,
                action="untimeout",
                reason="Báo cáo nhầm giveaway MrBeast",
            )
            return None
        return None

    @tasks.loop(minutes=5)
    async def expire_incidents(self) -> None:
        now = discord.utils.utcnow()
        try:
            pending = list(self.incidents.find({"status": "pending"}))
        except PyMongoError:
            logger.exception("Failed listing pending scam incidents")
            return
        for incident in pending:
            until = _as_aware(incident.get("timeout_until"))
            created = _as_aware(incident.get("created_at"))
            deadline = until or (
                created + timedelta(hours=TIMEOUT_HOURS) if created is not None else None
            )
            if deadline is None or deadline > now:
                continue
            claimed = self.incidents.find_one_and_update(
                {
                    "incident_id": incident["incident_id"],
                    "guild_id": incident["guild_id"],
                    "status": "pending",
                },
                {
                    "$set": {
                        "status": "expired",
                        "updated_at": now,
                        "resolved_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if claimed is None:
                continue
            await self._expire_panel(claimed)

    @expire_incidents.before_loop
    async def before_expire_incidents(self) -> None:
        await self.bot.wait_until_ready()

    async def _expire_panel(self, incident: dict) -> None:
        channel_id = incident.get("alert_channel_id")
        message_id = incident.get("alert_message_id")
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            return
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return
        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.add_field(
            name="Quyết định",
            value="Hết hạn bảng. Timeout 24 giờ vẫn giữ nếu Discord chưa gỡ.",
            inline=False,
        )
        try:
            await message.edit(
                embed=embed,
                view=self._disable_view(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Failed expiring scam panel incident=%s",
                incident.get("incident_id"),
            )

    @commands.command(
        name="scam_check",
        aliases=("mrbeast",),
        help="Xem tin được reply có giống dump ảnh giveaway giả không.",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(manage_messages=True)
    @commands.cooldown(1, SCAM_CHECK_COOLDOWN_SECONDS, commands.BucketType.user)
    async def scam_check(self, ctx: commands.Context) -> None:
        try:
            message = await fetch_same_channel_reply(ctx)
        except ReplyTargetError as exc:
            if "webhook" not in str(exc):
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            reference = ctx.message.reference
            resolved = getattr(reference, "resolved", None) if reference else None
            if not isinstance(resolved, discord.Message):
                await ctx.reply(
                    str(exc),
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            message = resolved

        image_count, other_count = count_image_attachments(message.attachments)
        dump = is_photo_dump_candidate(
            image_count,
            message.content or "",
            other_count,
        )
        text_score = self._score_message(message)
        now = discord.utils.utcnow()
        count = 0
        action = "watch"
        if dump:
            hit = self._window_hit(
                message,
                source=SOURCE_PHOTO_DUMP,
                image_count=image_count,
                fingerprint="",
                now=now,
            )
            count = photo_dump_count(self._window, hit)
            action = photo_dump_action(self._window, hit)
        embed = discord.Embed(
            title="MrBeast scam check",
            color=discord.Color.orange() if dump or text_score.hit else discord.Color.green(),
        )
        embed.add_field(
            name="Dump ảnh",
            value=(
                f"{'Có' if dump else 'Không'} · {image_count} ảnh"
                + (f" · {other_count} file khác" if other_count else "")
            ),
            inline=False,
        )
        embed.add_field(
            name="Cửa sổ 2 phút",
            value=(
                f"{count} dump của tác giả này · {action}"
                if dump
                else "Không áp dụng (không phải dump ảnh)"
            ),
            inline=False,
        )
        embed.add_field(
            name="Văn bản",
            value=(
                f"{'Khớp' if text_score.hit else 'Không khớp'} · "
                f"{text_score.reason}"
            )[:1024],
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Lần {WARNING_DUMP_COUNT}: cảnh báo 30s. "
                f"Lần {IMMEDIATE_TIMEOUT_DUMP_COUNT}: timeout ngay."
                if dump
                else "Lệnh này không xóa hay timeout."
            )
        )
        await ctx.reply(
            embed=embed,
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    cog = MrBeastScamCog(bot)
    bot.add_view(MrBeastScamDecisionView(cog))
    await bot.add_cog(cog)
