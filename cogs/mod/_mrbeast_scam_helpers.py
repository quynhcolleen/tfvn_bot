"""Pure scoring and window rules for the MrBeast photo-dump scam filter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import ipaddress
import re
import unicodedata


WINDOW_SECONDS = 120
WINDOW_MAX_ROWS = 200
MAX_DUMP_IMAGES = 4
MAX_DUMP_CAPTION_CHARS = 80
TIMEOUT_HOURS = 24
CHALLENGE_SECONDS = 30
WARNING_DUMP_COUNT = 3
IMMEDIATE_TIMEOUT_DUMP_COUNT = 5
ACTION_WATCH = "watch"
ACTION_CHALLENGE = "challenge"
ACTION_TIMEOUT = "timeout"
MAX_SNAPSHOT_CHARS = 300
MAX_ALERT_FILES = 4
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

SOURCE_PHOTO_DUMP = "photo_dump"
SOURCE_TEXT = "text"

ZERO_WIDTH_CHARS = frozenset(
    {
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\ufeff",
        "\u180e",
        "\u00ad",
    }
)
HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "ѕ": "s",
        "і": "i",
        "ӏ": "l",
        "ԁ": "d",
        "ɡ": "g",
        "ｍ": "m",
        "ｒ": "r",
        "ｂ": "b",
        "ｔ": "t",
    }
)
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_BRAND_COMPACT_RE = re.compile(r"mrbeast")
_BRAND_SPACED_RE = re.compile(r"\bmr[\s._-]*beast\b")
_BRAND_EXTRA = ("feastables", "jimmy donaldson", "jimmydonaldson")
_PRIZE_TERMS = (
    "giveaway",
    "winner",
    "congratulations",
    "congratulation",
    "nitro",
    "steam",
    "tesla",
    "iphone",
    "gift card",
    "giftcard",
    "trung thuong",
    "trúng thưởng",
    "nhan qua",
    "nhận quà",
    "nhan thuong",
    "nhận thưởng",
    "$10,000",
    "$10000",
    "10000$",
    "10.000",
)
_CTA_TERMS = (
    "click here",
    "click the link",
    "claim",
    "verify",
    "dm me",
    "inbox",
    "scan qr",
    "scan the qr",
    "bam vao",
    "bấm vào",
    "nhan link",
    "nhấn link",
    "nhan ngay",
    "nhận ngay",
)
_PHISHING_HOST_MARKERS = ("nitro", "steam", "gift", "giveaway", "free-discord")
_SHORTENER_HOSTS = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "cutt.ly",
        "t.ly",
        "rb.gy",
        "is.gd",
        "ow.ly",
    }
)
_ALLOWED_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "discord.com",
        "discordapp.com",
        "discordapp.net",
        "x.com",
        "twitter.com",
        "instagram.com",
        "tiktok.com",
        "feastables.com",
        "cdn.discordapp.com",
        "media.discordapp.net",
        "media.discordapp.com",
    }
)


@dataclass(frozen=True)
class TextScore:
    hit: bool
    score: int
    signals: tuple[str, ...]
    lure_hosts: tuple[str, ...]
    fingerprint: str
    reason: str


@dataclass(frozen=True)
class WindowHit:
    guild_id: int
    author_id: int
    parent_channel_id: int
    channel_id: int
    message_id: int
    source: str
    image_count: int
    fingerprint: str
    created_at: datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parent_channel_id(channel: object) -> int:
    """Count threads with their parent so a forum raid is one channel."""
    parent_id = getattr(channel, "parent_id", None)
    if isinstance(parent_id, int) and parent_id > 0:
        return parent_id
    channel_id = getattr(channel, "id", None)
    if isinstance(channel_id, int) and channel_id > 0:
        return channel_id
    raise ValueError("Channel is missing a Discord id")


def is_image_attachment(attachment: object) -> bool:
    """True for Discord image uploads; videos and other files are false."""
    content_type = str(getattr(attachment, "content_type", None) or "").lower()
    content_type = content_type.split(";", 1)[0].strip()
    if content_type.startswith("image/"):
        return True
    filename = str(getattr(attachment, "filename", "")).lower()
    if Path(filename).suffix in _IMAGE_EXTENSIONS:
        return True
    if not Path(filename).suffix and content_type in ("", "application/octet-stream"):
        width = getattr(attachment, "width", None)
        height = getattr(attachment, "height", None)
        if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
            return True
    return False


def count_image_attachments(attachments: Any) -> tuple[int, int]:
    """Return (image_count, other_attachment_count)."""
    image_count = 0
    other_count = 0
    for attachment in attachments or ():
        if is_image_attachment(attachment):
            image_count += 1
        else:
            other_count += 1
    return image_count, other_count


def normalize_message_text(value: str) -> str:
    """NFKC, homoglyph fold, strip format chars, collapse whitespace."""
    normalized = unicodedata.normalize("NFKC", value or "")
    stripped = "".join(
        char
        for char in normalized.translate(HOMOGLYPHS)
        if unicodedata.category(char) != "Cf" and char not in ZERO_WIDTH_CHARS
    )
    return " ".join(stripped.casefold().split())


def compact_brand_text(value: str) -> str:
    """Drop spaces and `._-` so m.r.b.e.a.s.t still matches."""
    return re.sub(r"[\s._-]+", "", normalize_message_text(value))


def caption_without_urls(content: str) -> str:
    return " ".join(_URL_RE.sub(" ", content or "").split())


def is_photo_dump_candidate(
    image_count: int,
    content: str,
    other_attachment_count: int = 0,
) -> bool:
    """1–4 photos with an empty or tiny caption; no extra non-image files."""
    if other_attachment_count > 0:
        return False
    if image_count < 1 or image_count > MAX_DUMP_IMAGES:
        return False
    caption = caption_without_urls(content)
    return len(caption) <= MAX_DUMP_CAPTION_CHARS


def extract_hosts(content: str) -> tuple[str, ...]:
    hosts: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(content or ""):
        raw = match.group(0).rstrip(")]>,.")
        try:
            host = (urlsplit(raw).hostname or "").casefold()
        except ValueError:
            continue
        if host.startswith("www."):
            host = host[4:]
        if not host or host in seen:
            continue
        seen.add(host)
        hosts.append(host)
    return tuple(hosts)


def _host_allowed(host: str) -> bool:
    for allowed in _ALLOWED_HOSTS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _is_ip_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_phishing_host(host: str) -> bool:
    if _host_allowed(host):
        return False
    if host in _SHORTENER_HOSTS or _is_ip_host(host):
        return True
    if host.endswith(".gift"):
        return True
    return any(marker in host for marker in _PHISHING_HOST_MARKERS)


def lure_hosts_from(content: str) -> tuple[str, ...]:
    return tuple(host for host in extract_hosts(content) if not _host_allowed(host))


def collect_text_corpus(
    content: str,
    *,
    author_names: tuple[str, ...] = (),
    embed_texts: tuple[str, ...] = (),
    attachment_names: tuple[str, ...] = (),
    sticker_names: tuple[str, ...] = (),
) -> str:
    parts = [content, *author_names, *embed_texts, *attachment_names, *sticker_names]
    return "\n".join(part for part in parts if part)


def _contains_any(haystack: str, terms: tuple[str, ...]) -> bool:
    return any(term in haystack for term in terms)


def score_text(
    corpus: str,
    *,
    author_names: tuple[str, ...] = (),
    mention_everyone: bool = False,
) -> TextScore:
    """Conservative caption-scam score. Brand mention alone is never a hit."""
    normalized = normalize_message_text(corpus)
    compact = compact_brand_text(corpus)
    author_normalized = normalize_message_text(" ".join(author_names))
    author_compact = compact_brand_text(" ".join(author_names))

    brand = bool(
        _BRAND_COMPACT_RE.search(compact)
        or _BRAND_SPACED_RE.search(normalized)
        or _contains_any(normalized, _BRAND_EXTRA)
        or _contains_any(compact, _BRAND_EXTRA)
    )
    impersonation = bool(
        _BRAND_COMPACT_RE.search(author_compact)
        or _BRAND_SPACED_RE.search(author_normalized)
        or _contains_any(author_normalized, _BRAND_EXTRA)
    )
    prize = _contains_any(normalized, _PRIZE_TERMS)
    cta = _contains_any(normalized, _CTA_TERMS)
    hosts = lure_hosts_from(corpus)
    lure_link = bool(hosts)
    phishing = any(is_phishing_host(host) for host in hosts)
    everyone = mention_everyone

    signals: list[str] = []
    score = 0
    if brand:
        signals.append("brand")
        score += 2
    if impersonation:
        signals.append("impersonation")
        score += 3
    if prize:
        signals.append("prize")
        score += 2
    if cta:
        signals.append("cta")
        score += 2
    if lure_link:
        signals.append("lure_link")
        score += 2
    if phishing:
        signals.append("phishing_host")
        score += 2
    if everyone:
        signals.append("everyone")
        score += 1

    hit = (
        (brand and prize and (cta or lure_link))
        or (impersonation and (prize or cta or lure_link))
        or (brand and phishing)
    )
    fingerprint = "|".join(hosts) if hosts else ""
    if hit and not fingerprint:
        fingerprint = "text:" + ",".join(signals)
    reason = ", ".join(signals) if signals else "no signals"
    return TextScore(
        hit=hit,
        score=score,
        signals=tuple(signals),
        lure_hosts=hosts,
        fingerprint=fingerprint,
        reason=reason,
    )


def prune_window(
    rows: list[WindowHit],
    now: datetime,
    *,
    window_seconds: int = WINDOW_SECONDS,
    max_rows: int = WINDOW_MAX_ROWS,
) -> list[WindowHit]:
    cutoff = _as_utc(now) - timedelta(seconds=window_seconds)
    kept = [row for row in rows if _as_utc(row.created_at) >= cutoff]
    if len(kept) > max_rows:
        return kept[-max_rows:]
    return kept


def upsert_window(rows: list[WindowHit], hit: WindowHit, now: datetime) -> list[WindowHit]:
    updated = [row for row in rows if row.message_id != hit.message_id]
    updated.append(hit)
    return prune_window(updated, now)


def photo_dump_channel_ids(rows: list[WindowHit], hit: WindowHit) -> set[int]:
    channels = {hit.parent_channel_id}
    for row in rows:
        if (
            row.guild_id == hit.guild_id
            and row.author_id == hit.author_id
            and row.source == SOURCE_PHOTO_DUMP
        ):
            channels.add(row.parent_channel_id)
    return channels


def photo_dump_count(rows: list[WindowHit], hit: WindowHit) -> int:
    """How many photo dumps this author has in the current window, including hit."""
    if hit.source != SOURCE_PHOTO_DUMP:
        return 0
    return sum(
        1
        for row in rows
        if row.guild_id == hit.guild_id
        and row.author_id == hit.author_id
        and row.source == SOURCE_PHOTO_DUMP
    )


def photo_dump_action(rows: list[WindowHit], hit: WindowHit) -> str:
    """Watch until the 3rd dump; warn on 3–4; timeout immediately on the 5th."""
    count = photo_dump_count(rows, hit)
    if count >= IMMEDIATE_TIMEOUT_DUMP_COUNT:
        return ACTION_TIMEOUT
    if count >= WARNING_DUMP_COUNT:
        return ACTION_CHALLENGE
    return ACTION_WATCH


def should_escalate_photo_dump(rows: list[WindowHit], hit: WindowHit) -> bool:
    """True when a dump should warn or timeout (3rd dump or later)."""
    return photo_dump_action(rows, hit) != ACTION_WATCH


def should_escalate_text(rows: list[WindowHit], hit: WindowHit) -> bool:
    """Same author or same lure fingerprint in two parent channels."""
    if hit.source != SOURCE_TEXT or not hit.fingerprint:
        return False
    channels = {hit.parent_channel_id}
    for row in rows:
        if row.guild_id != hit.guild_id or row.source != SOURCE_TEXT:
            continue
        same_author = row.author_id == hit.author_id
        same_lure = bool(row.fingerprint) and row.fingerprint == hit.fingerprint
        if same_author or same_lure:
            channels.add(row.parent_channel_id)
    return len(channels) >= 2


def related_photo_hits(rows: list[WindowHit], hit: WindowHit) -> tuple[WindowHit, ...]:
    related = [
        row
        for row in rows
        if row.guild_id == hit.guild_id
        and row.author_id == hit.author_id
        and row.source == SOURCE_PHOTO_DUMP
    ]
    if hit not in related:
        related.append(hit)
    return tuple(related)


def truncate_snapshot(value: str, limit: int = MAX_SNAPSHOT_CHARS) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def timeout_until(now: datetime, hours: int = TIMEOUT_HOURS) -> datetime:
    return _as_utc(now) + timedelta(hours=hours)
