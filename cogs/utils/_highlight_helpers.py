"""Pure vote-count, spacing, and channel helpers for automatic highlights."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cogs.operation._setup_helpers import parse_discord_id


SKULL_EMOJI = "\N{SKULL}"
# Unique non-bot 💀 reactions required before a message can be highlighted.
HIGHLIGHT_THRESHOLD = 1
# Minimum seconds between highlight posts in the same guild.
HIGHLIGHT_MIN_INTERVAL_SECONDS = 60
HIGHLIGHT_CHANNEL_VARIABLE = "HIGHLIGHT_CHANNEL"
HIGHLIGHT_COLLECTION = "highlight_nominations"
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

STATUS_PENDING = "pending"
STATUS_POSTING = "posting"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_POSTING)

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class HighlightLookupError(Exception):
    """A source message could not be turned into a highlight."""


class HighlightConfigError(Exception):
    """Highlight channel configuration is missing or invalid."""


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def seconds_until_highlight_slot(
    last_posted_at: datetime | None,
    *,
    now: datetime,
    min_interval_seconds: int = HIGHLIGHT_MIN_INTERVAL_SECONDS,
) -> float:
    """Seconds to wait before the next guild highlight may post."""
    if last_posted_at is None:
        return 0.0
    elapsed = (as_utc(now) - as_utc(last_posted_at)).total_seconds()
    remaining = min_interval_seconds - elapsed
    return 0.0 if remaining <= 0 else remaining


def is_skull_emoji(emoji: object) -> bool:
    """Return whether a Discord emoji value is unicode 💀."""
    if emoji is None:
        return False
    name = getattr(emoji, "name", None)
    if name == SKULL_EMOJI:
        return True
    return str(emoji) == SKULL_EMOJI


def count_unique_skull_voters(
    user_ids: list[Any] | tuple[Any, ...] | set[Any],
    *,
    bot_user_id: int | None = None,
) -> int:
    """Count distinct voter IDs, dropping the bot when given."""
    unique: set[int] = set()
    for raw in user_ids:
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            continue
        if user_id <= 0:
            continue
        unique.add(user_id)
    if bot_user_id is not None:
        unique.discard(int(bot_user_id))
    return len(unique)


def should_post_highlight(
    status: str,
    unique_count: int,
    threshold: int = HIGHLIGHT_THRESHOLD,
) -> bool:
    """Return whether a pending highlight has reached the skull threshold."""
    return status == STATUS_PENDING and unique_count >= threshold


def parse_highlight_channel_id(value: object) -> int:
    """Parse HIGHLIGHT_CHANNEL from Mongo global variables."""
    channel_id = parse_discord_id(value)
    if channel_id is None:
        raise HighlightConfigError(
            "HIGHLIGHT_CHANNEL is not set in global variables."
        )
    return channel_id


def channel_is_nsfw(channel: object) -> bool:
    checker = getattr(channel, "is_nsfw", None)
    if callable(checker):
        return bool(checker())
    return bool(getattr(channel, "nsfw", False))


def first_image_attachment(attachments: list[Any] | tuple[Any, ...] | None) -> Any | None:
    """Return the first image-like Discord attachment, if any."""
    for attachment in attachments or ():
        content_type = str(getattr(attachment, "content_type", None) or "").lower()
        content_type = content_type.split(";", 1)[0].strip()
        if content_type.startswith("image/"):
            return attachment
        filename = str(getattr(attachment, "filename", "")).lower()
        if Path(filename).suffix in _IMAGE_EXTENSIONS:
            return attachment
        # Some pasted uploads have neither a MIME type nor a filename extension.
        # Discord's dimensions still identify them as media; known file types
        # such as videos must not take a slot in the image gallery.
        if not Path(filename).suffix and content_type in ("", "application/octet-stream"):
            width = getattr(attachment, "width", None)
            height = getattr(attachment, "height", None)
            if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
                return attachment
    return None


def has_renderable_content(*, text: str, has_image: bool) -> bool:
    return bool(text.strip()) or has_image


def ignore_reaction_user(
    *,
    user_id: int,
    bot_user_id: int | None,
    member: object | None = None,
) -> bool:
    """Skip the bot and other bot accounts when counting skulls."""
    if bot_user_id is not None and int(user_id) == int(bot_user_id):
        return True
    if member is not None and getattr(member, "bot", False):
        return True
    return False


def _remember_voter(
    user: object,
    seen: set[int],
    ordered: list[int],
    bot_user_id: int | None,
) -> None:
    if getattr(user, "bot", False):
        return
    try:
        user_id = int(getattr(user, "id"))
    except (TypeError, ValueError):
        return
    if user_id <= 0:
        return
    if bot_user_id is not None and user_id == int(bot_user_id):
        return
    if user_id in seen:
        return
    seen.add(user_id)
    ordered.append(user_id)


async def collect_skull_voter_ids(
    message: object,
    *,
    bot_user_id: int | None,
) -> list[int]:
    """Unique non-bot 💀 reactors on a Discord message, in first-seen order."""
    seen: set[int] = set()
    ordered: list[int] = []
    for reaction in getattr(message, "reactions", None) or ():
        if not is_skull_emoji(getattr(reaction, "emoji", None)):
            continue
        users = reaction.users()
        if hasattr(users, "__aiter__"):
            async for user in users:
                _remember_voter(user, seen, ordered, bot_user_id)
        else:
            for user in users:
                _remember_voter(user, seen, ordered, bot_user_id)
    return ordered


def format_highlight_caption(jump_url: str) -> str:
    return f"🔗 [Tin nhắn gốc]({jump_url})"


def format_highlight_congrats(
    author_mention: str,
    highlight_jump_url: str | None = None,
) -> str:
    """Vietnamese reply on the source message after it lands on TV."""
    if highlight_jump_url:
        return (
            f"📺 Chúc mừng {author_mention}, tin nhắn này đã lên "
            f"[TV]({highlight_jump_url})!"
        )
    return f"📺 Chúc mừng {author_mention}, tin nhắn này đã lên TV!"
