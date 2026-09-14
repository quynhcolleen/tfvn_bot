"""Pure helpers for the marriage feature (no Discord / Mongo I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


XP_PER_INTERACTION = 5
XP_PER_LEVEL = 20


@dataclass(frozen=True)
class RankInfo:
    key: str
    display: str
    emoji: str
    color: int
    min_level: int


# Ordered lowest → highest. Rebalance only here.
MARRIAGE_RANKS: tuple[RankInfo, ...] = (
    RankInfo("bronze", "Đồng", "🥉", 0xCD7F32, 1),
    RankInfo("silver", "Bạc", "🥈", 0xC0C0C0, 5),
    RankInfo("gold", "Vàng", "🥇", 0xFFD700, 10),
    RankInfo("diamond", "Kim cương", "💎", 0xB9F2FF, 18),
    RankInfo("blue_sapphire", "Sapphire xanh", "🔵", 0x0F52BA, 28),
    RankInfo("amethyst", "Thạch anh tím", "💜", 0x9966CC, 40),
    RankInfo("ruby", "Hồng ngọc", "❤️", 0xE0115F, 55),
    RankInfo("emerald", "Lục bảo", "💚", 0x50C878, 75),
    RankInfo("obsidian", "Obsidian", "🖤", 0x1C1C1C, 100),
    RankInfo("eternal", "Vĩnh cửu", "♾️", 0xFF69B4, 150),
)


def normalize_pair(user_id_a: int, user_id_b: int) -> tuple[int, int]:
    """Return sorted pair ids so (a,b) and (b,a) map to the same document."""
    if user_id_a == user_id_b:
        raise ValueError("pair members must be different users")
    if user_id_a < user_id_b:
        return user_id_a, user_id_b
    return user_id_b, user_id_a


def is_pair(left: int, right: int, user_a: int, user_b: int) -> bool:
    try:
        return normalize_pair(left, right) == (user_a, user_b)
    except ValueError:
        return False


def level_from_xp(xp: int) -> int:
    if xp < 0:
        raise ValueError("xp must be non-negative")
    return (xp // XP_PER_LEVEL) + 1


def rank_from_level(level: int) -> RankInfo:
    if level < 1:
        raise ValueError("level must be >= 1")
    current = MARRIAGE_RANKS[0]
    for rank in MARRIAGE_RANKS:
        if level >= rank.min_level:
            current = rank
        else:
            break
    return current


def rank_from_xp(xp: int) -> RankInfo:
    return rank_from_level(level_from_xp(xp))


def xp_progress_in_level(xp: int) -> tuple[int, int]:
    """Return (xp into current level band, xp needed per level)."""
    if xp < 0:
        raise ValueError("xp must be non-negative")
    return xp % XP_PER_LEVEL, XP_PER_LEVEL


def next_rank(level: int) -> RankInfo | None:
    """Rank after the current one, or None if already max."""
    current = rank_from_level(level)
    for rank in MARRIAGE_RANKS:
        if rank.min_level > current.min_level:
            return rank
    return None


def progress_bar(ratio: float, segments: int = 10) -> str:
    """Build a text bar from a 0..1 ratio."""
    if segments < 1:
        raise ValueError("segments must be >= 1")
    clamped = max(0.0, min(1.0, ratio))
    filled = int(round(clamped * segments))
    filled = max(0, min(segments, filled))
    return "█" * filled + "░" * (segments - filled)


def level_progress_bar(xp: int, segments: int = 10) -> str:
    into, need = xp_progress_in_level(xp)
    return progress_bar(into / need if need else 1.0, segments=segments)


def rank_level_progress_bar(level: int, segments: int = 10) -> str:
    """Progress within current rank band toward the next rank's min_level."""
    current = rank_from_level(level)
    following = next_rank(level)
    if following is None:
        return progress_bar(1.0, segments=segments)
    span = following.min_level - current.min_level
    if span <= 0:
        return progress_bar(1.0, segments=segments)
    into = level - current.min_level
    return progress_bar(into / span, segments=segments)


def days_together(married_at, now) -> int:
    """Whole days between married_at and now (both timezone-aware or naive)."""
    delta = now - married_at
    return max(0, int(delta.total_seconds() // 86400))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def partner_id_of(marriage: Mapping[str, Any], user_id: int) -> int:
    """Return the other partner's Discord id."""
    user_a = int(marriage["user_a"])
    user_b = int(marriage["user_b"])
    if user_id == user_a:
        return user_b
    if user_id == user_b:
        return user_a
    raise ValueError("user is not a partner in this marriage")


@dataclass(frozen=True)
class MarriageCardInfo:
    partner_id: int
    rank: RankInfo
    level: int
    married_on: str | None
    days_together: int | None


def marriage_card_info(
    marriage: Mapping[str, Any],
    user_id: int,
    *,
    now: datetime,
) -> MarriageCardInfo:
    """Summarize an active marriage for a member card."""
    xp = int(marriage.get("xp", 0))
    level = int(marriage.get("level") or level_from_xp(xp))
    rank = rank_from_level(level)
    married_at = marriage.get("married_at")
    married_on = None
    together = None
    if isinstance(married_at, datetime):
        married_utc = _as_utc(married_at)
        married_on = married_utc.strftime("%Y-%m-%d")
        together = days_together(married_utc, _as_utc(now))
    return MarriageCardInfo(
        partner_id=partner_id_of(marriage, user_id),
        rank=rank,
        level=level,
        married_on=married_on,
        days_together=together,
    )


def format_marriage_card_value(
    marriage: Mapping[str, Any] | None,
    user_id: int,
    *,
    now: datetime,
) -> str:
    """Vietnamese field text for a femboy/member card."""
    if marriage is None:
        return "Độc thân ✨"
    info = marriage_card_info(marriage, user_id, now=now)
    lines = [
        f"❤️ <@{info.partner_id}>",
        f"{info.rank.emoji} **{info.rank.display}** · Level **{info.level}**",
    ]
    if info.married_on is not None:
        together = (
            f" · **{info.days_together} ngày** bên nhau"
            if info.days_together is not None
            else ""
        )
        lines.append(f"📅 {info.married_on}{together}")
    elif info.days_together is not None:
        lines.append(f"**{info.days_together} ngày** bên nhau")
    return "\n".join(lines)
