"""Bounded Discord attachments and embed snapshots for highlight cards."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlsplit

from cogs.utils._highlight_helpers import first_image_attachment


MAX_HIGHLIGHT_IMAGES = 4
MAX_HIGHLIGHT_EMBEDS = 4
_DISCORD_MEDIA_HOSTS = {
    "cdn.discordapp.com",
    "cdn.discordapp.net",
    "media.discordapp.com",
    "media.discordapp.net",
}
_MENTION_OR_CODE = re.compile(
    r"(?P<code>(?P<fence>`+)[\s\S]*?(?P=fence))|"
    r"(?<!\\)<(?P<kind>@[!&]?|#)(?P<id>[0-9]{1,20})>"
)
_MARKDOWN_CHARACTER = re.compile(r"([\\`*_~|\[\]])")


@dataclass(frozen=True)
class HighlightEmbed:
    """Plain embed data; downloaded pixels are filled in before rendering."""

    title: str = ""
    description: str = ""
    author_name: str = ""
    footer_text: str = ""
    fields: tuple[tuple[str, str], ...] = ()
    color_rgb: tuple[int, int, int] | None = None
    image_bytes: bytes | None = None
    thumbnail_bytes: bytes | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None

    @property
    def has_text(self) -> bool:
        return bool(
            self.title or self.description or self.author_name
            or self.footer_text or self.fields
        )


@dataclass(frozen=True)
class HighlightMedia:
    attachments: tuple[Any, ...] = ()
    embeds: tuple[HighlightEmbed, ...] = ()

    @property
    def has_content(self) -> bool:
        return bool(self.attachments or self.embeds)


def discord_media_url(value: object) -> str | None:
    """Use Discord's CDN/proxy only, never fetch user-supplied external hosts."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or not parsed.path.startswith("/")
        ):
            return None
    except ValueError:
        return None
    if host in _DISCORD_MEDIA_HOSTS or re.fullmatch(
        r"images-ext-[0-9]+\.discordapp\.net", host
    ):
        return value
    return None


def media_url_key(url: str) -> str:
    """Ignore CDN signatures when matching repeated attachment references."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host in _DISCORD_MEDIA_HOSTS and parsed.path.startswith("/attachments/"):
        return parsed.path
    return url


def _embed_image_url(image: object, attachments: tuple[Any, ...]) -> str | None:
    for value in (getattr(image, "proxy_url", None), getattr(image, "url", None)):
        if isinstance(value, str) and value.startswith("attachment://"):
            filename = value.removeprefix("attachment://")
            for attachment in attachments:
                if getattr(attachment, "filename", None) == filename:
                    value = getattr(attachment, "url", None)
                    break
        safe_url = discord_media_url(value)
        if safe_url:
            return safe_url
    return None


def _text(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _mention_name(message: object, kind: str, target_id: int) -> str:
    """Resolve embed-only mentions using the guild cache and source context."""
    guild = getattr(message, "guild", None)
    if kind == "#":
        getters = ("get_channel_or_thread", "get_channel", "get_thread")
        candidates = list(getattr(message, "channel_mentions", None) or ())
        candidates.append(getattr(message, "channel", None))
        fallback = "kênh không xác định"
    elif kind == "@&":
        getters = ("get_role",)
        candidates = list(getattr(message, "role_mentions", None) or ())
        fallback = "role không xác định"
    else:
        getters = ("get_member",)
        candidates = list(getattr(message, "mentions", None) or ())
        candidates.append(getattr(message, "author", None))
        fallback = "người dùng không xác định"
    for getter_name in getters:
        getter = getattr(guild, getter_name, None)
        if callable(getter):
            candidate = getter(target_id)
            if candidate is not None:
                candidates.insert(0, candidate)
                break
    for candidate in candidates:
        if getattr(candidate, "id", None) != target_id:
            continue
        label = getattr(candidate, "display_name", None) or getattr(candidate, "name", None)
        if isinstance(label, str) and label:
            # A nickname containing Markdown must stay literal when the embed is styled.
            return _MARKDOWN_CHARACTER.sub(r"\\\1", label.replace("\n", " "))
    return fallback


def clean_embed_mentions(value: str, message: object) -> str:
    """Render member, role, and channel references as names outside code spans."""
    def replace(match: re.Match[str]) -> str:
        if match.group("code") is not None:
            return match.group(0)
        kind = match.group("kind")
        prefix = "#" if kind == "#" else "@"
        return prefix + _mention_name(message, kind, int(match.group("id")))

    return _MENTION_OR_CODE.sub(replace, value)


def collect_highlight_media(message: object) -> HighlightMedia:
    """Snapshot visible image attachments and rich/link-preview embeds."""
    def embed_text(value: object, limit: int) -> str:
        return clean_embed_mentions(_text(value, limit), message)[:limit]

    attachments = tuple(
        attachment
        for attachment in (getattr(message, "attachments", None) or ())
        if first_image_attachment([attachment]) is not None
    )
    snapshots: list[HighlightEmbed] = []
    for embed in (getattr(message, "embeds", None) or ())[:10]:
        color = getattr(embed, "color", None)
        image_url = _embed_image_url(getattr(embed, "image", None), attachments)
        if not image_url and getattr(embed, "type", None) == "image":
            image_url = discord_media_url(getattr(embed, "url", None))
        thumbnail_url = _embed_image_url(getattr(embed, "thumbnail", None), attachments)
        snapshot = HighlightEmbed(
            title=embed_text(getattr(embed, "title", None), 256),
            description=embed_text(getattr(embed, "description", None), 4096),
            author_name=embed_text(getattr(getattr(embed, "author", None), "name", None), 256),
            footer_text=embed_text(getattr(getattr(embed, "footer", None), "text", None), 2048),
            fields=tuple(
                (name, value)
                for field in (getattr(embed, "fields", None) or ())[:25]
                for name, value in [(
                    embed_text(getattr(field, "name", None), 256),
                    embed_text(getattr(field, "value", None), 1024),
                )]
                if name or value
            ),
            color_rgb=color.to_rgb() if color is not None else None,
            image_url=image_url,
            thumbnail_url=thumbnail_url,
        )
        if snapshot.has_text or image_url or thumbnail_url:
            snapshots.append(snapshot)
            if len(snapshots) == MAX_HIGHLIGHT_EMBEDS:
                break
    return HighlightMedia(attachments[:MAX_HIGHLIGHT_IMAGES], tuple(snapshots))
