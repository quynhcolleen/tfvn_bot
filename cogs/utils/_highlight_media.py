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


def collect_highlight_media(message: object) -> HighlightMedia:
    """Snapshot visible image attachments and rich/link-preview embeds."""
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
            title=_text(getattr(embed, "title", None), 256),
            description=_text(getattr(embed, "description", None), 4096),
            author_name=_text(getattr(getattr(embed, "author", None), "name", None), 256),
            footer_text=_text(getattr(getattr(embed, "footer", None), "text", None), 2048),
            fields=tuple(
                (name, value)
                for field in (getattr(embed, "fields", None) or ())[:25]
                for name, value in [(
                    _text(getattr(field, "name", None), 256),
                    _text(getattr(field, "value", None), 1024),
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
