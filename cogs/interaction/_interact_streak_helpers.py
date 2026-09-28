"""Pure helpers for guild-scoped pair interaction streaks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any


VIETNAM_TIMEZONE = timezone(timedelta(hours=7), name="UTC+07:00")

SOURCE_MENTION = "mention"
SOURCE_REPLY = "reply"
SOURCE_VOICE = "voice"
VALID_SOURCES = frozenset({SOURCE_MENTION, SOURCE_REPLY, SOURCE_VOICE})

SOURCE_LABELS = {
    SOURCE_MENTION: "tin nhắn",
    SOURCE_REPLY: "trả lời",
    SOURCE_VOICE: "voice",
}

VOICE_OVERLAP_SECONDS = 300
STREAK_LIST_LIMIT = 10
MILESTONE_DAYS = frozenset({3, 7, 30, 100})


@dataclass(frozen=True, slots=True)
class StreakPartner:
    user_id: int
    source: str


@dataclass(frozen=True, slots=True)
class StreakCreditResult:
    document: dict[str, Any]
    advanced: bool


def normalize_pair(user_id_a: int, user_id_b: int) -> tuple[int, int]:
    """Return sorted pair ids so (a,b) and (b,a) map to the same document."""
    if user_id_a == user_id_b:
        raise ValueError("pair members must be different users")
    if user_id_a < user_id_b:
        return user_id_a, user_id_b
    return user_id_b, user_id_a


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating naive values as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_mongo_utc(value: datetime) -> datetime:
    """Return naive UTC at MongoDB's BSON millisecond precision."""
    normalized = as_utc(value).replace(tzinfo=None)
    return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)


def vietnam_date(now: datetime) -> date:
    """Calendar date in Vietnam time (UTC+7)."""
    return as_utc(now).astimezone(VIETNAM_TIMEZONE).date()


def iso_date(value: date) -> str:
    return value.isoformat()


def previous_iso_date(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()


def live_dates(today: str) -> tuple[str, str]:
    return today, previous_iso_date(today)


def is_live_streak(last_active_date: str | None, today: str) -> bool:
    if not last_active_date:
        return False
    return last_active_date in live_dates(today)


def live_streak_query(guild_id: int, today: str) -> dict[str, Any]:
    return {
        "guild_id": guild_id,
        "last_active_date": {"$in": list(live_dates(today))},
        "current_streak": {"$gt": 0},
    }


def displayed_current_streak(document: Mapping[str, Any], today: str) -> int:
    if not is_live_streak(document.get("last_active_date"), today):
        return 0
    return int(document.get("current_streak") or 0)


def other_user_id(document: Mapping[str, Any], user_id: int) -> int:
    user_a = int(document["user_a"])
    user_b = int(document["user_b"])
    if user_id == user_a:
        return user_b
    if user_id == user_b:
        return user_a
    raise ValueError("user is not in pair")


def source_label(source: str | None) -> str:
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    return "không rõ"


def is_streak_milestone(current_streak: int) -> bool:
    return current_streak in MILESTONE_DAYS


def format_milestone_message(user_a: int, user_b: int, current_streak: int) -> str:
    return (
        f"🔥 <@{user_a}> và <@{user_b}> đã đạt chuỗi **{current_streak} ngày**!"
    )


def is_credit_eligible_voice_channel(
    channel_id: int | None,
    afk_channel_id: int | None,
) -> bool:
    if channel_id is None:
        return False
    if afk_channel_id is not None and channel_id == afk_channel_id:
        return False
    return True


def voice_overlap_partner_ids(
    member_id: int,
    occupants: Sequence[object],
) -> tuple[int, ...]:
    partners: list[int] = []
    seen: set[int] = set()
    for occupant in occupants:
        user_id = getattr(occupant, "id", None)
        if not isinstance(user_id, int) or user_id in seen:
            continue
        if user_id == member_id or bool(getattr(occupant, "bot", False)):
            continue
        seen.add(user_id)
        partners.append(user_id)
    return tuple(partners)


def partners_from_message(
    *,
    author_id: int,
    mentions: Sequence[object],
    reply_author: object | None,
) -> tuple[StreakPartner, ...]:
    """Human partners from user mentions and a resolved reply author.

    Reply wins when the same user is both mentioned and replied to.
    """
    by_id: dict[int, str] = {}
    for mentioned in mentions:
        user_id = getattr(mentioned, "id", None)
        if not isinstance(user_id, int):
            continue
        if user_id == author_id or bool(getattr(mentioned, "bot", False)):
            continue
        by_id[user_id] = SOURCE_MENTION
    if reply_author is not None:
        user_id = getattr(reply_author, "id", None)
        if (
            isinstance(user_id, int)
            and user_id != author_id
            and not bool(getattr(reply_author, "bot", False))
        ):
            by_id[user_id] = SOURCE_REPLY
    return tuple(
        StreakPartner(user_id=user_id, source=source)
        for user_id, source in by_id.items()
    )


def apply_streak_credit(
    document: Mapping[str, Any] | None,
    *,
    guild_id: int,
    user_id_a: int,
    user_id_b: int,
    today: str,
    yesterday: str,
    source: str,
    now: datetime,
) -> StreakCreditResult:
    """Return the next stored pair document and whether the Vietnam day advanced."""
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown streak source: {source}")
    user_a, user_b = normalize_pair(user_id_a, user_id_b)
    now_utc = to_mongo_utc(now)

    if document is not None and document.get("last_active_date") == today:
        return StreakCreditResult(dict(document), advanced=False)

    if document is not None and document.get("last_active_date") == yesterday:
        current = int(document.get("current_streak") or 0) + 1
    else:
        current = 1

    longest = current
    created_at = now_utc
    stored_id = None
    if document is not None:
        longest = max(int(document.get("longest_streak") or 0), current)
        created_at = document.get("created_at") or now_utc
        stored_id = document.get("_id")

    updated: dict[str, Any] = {
        "guild_id": guild_id,
        "user_a": user_a,
        "user_b": user_b,
        "partner_ids": [user_a, user_b],
        "current_streak": current,
        "longest_streak": longest,
        "last_active_date": today,
        "last_source": source,
        "created_at": created_at,
        "updated_at": now_utc,
    }
    if stored_id is not None:
        updated["_id"] = stored_id
    return StreakCreditResult(updated, advanced=True)
