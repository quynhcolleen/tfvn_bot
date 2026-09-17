"""Pure parsing and permission helpers for giveaway create CLI and Discord form."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass


MAX_WINNERS = 20
MIN_DURATION_SECONDS = 10
MAX_DURATION_SECONDS = 60 * 60 * 24 * 30  # 30 days
MAX_PRIZE_CHARS = 1024
MAX_DURATION_INPUT_CHARS = 20
MAX_WINNERS_INPUT_CHARS = 2
MAX_GIVEAWAY_ROLES = 25
MIN_BONUS_MULTIPLIER = 2
MAX_BONUS_MULTIPLIER = 20
DEFAULT_BONUS_MULTIPLIER = 2
MAX_ROLE_MENTION_CHARS = 1024


class GiveawayFormError(ValueError):
    """User-facing validation error for giveaway create fields."""


class GiveawayCreateError(RuntimeError):
    """Failed to post or persist a giveaway after validation succeeded."""


@dataclass(frozen=True, slots=True)
class GiveawayDraft:
    prize: str
    winner_count: int
    seconds: int


@dataclass(frozen=True, slots=True)
class GiveawayRoleSettings:
    """Guild or per-giveaway blacklist and bonus-win roles."""

    blacklist_role_ids: tuple[int, ...] = ()
    bonus_role_ids: tuple[int, ...] = ()
    bonus_multiplier: int = DEFAULT_BONUS_MULTIPLIER

    def to_document(self) -> dict[str, object]:
        return {
            "blacklist_role_ids": list(self.blacklist_role_ids),
            "bonus_role_ids": list(self.bonus_role_ids),
            "bonus_multiplier": int(self.bonus_multiplier),
        }


def can_manage_giveaway(member: object) -> bool:
    """Administrator, Manage Server, or Manage Messages may create giveaways."""
    permissions = getattr(member, "guild_permissions", None)
    if permissions is None:
        return False
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
        or getattr(permissions, "manage_messages", False)
    )


def format_duration(seconds: int) -> str:
    units = [
        ("ngày", 86400),
        ("giờ", 3600),
        ("phút", 60),
        ("giây", 1),
    ]
    remaining = int(seconds)
    parts: list[str] = []
    for name, unit_seconds in units:
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(f"{value} {name}")
    return " ".join(parts) if parts else "0 giây"


def parse_duration(duration: str) -> int:
    matches = re.findall(r"(\d+)([dhms])", duration.lower().replace(" ", ""))
    if not matches:
        raise GiveawayFormError(
            "Thời gian không hợp lệ. Sử dụng định dạng như `10m`, `1h30m`, hoặc `2d`."
        )

    total_seconds = 0
    for value, unit in matches:
        amount = int(value)
        if unit == "d":
            total_seconds += amount * 86400
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "m":
            total_seconds += amount * 60
        elif unit == "s":
            total_seconds += amount

    if total_seconds <= 0:
        raise GiveawayFormError("Thời gian phải lớn hơn 0.")
    if total_seconds < MIN_DURATION_SECONDS:
        raise GiveawayFormError(
            f"Thời gian tối thiểu là {MIN_DURATION_SECONDS} giây."
        )
    if total_seconds > MAX_DURATION_SECONDS:
        raise GiveawayFormError(
            f"Thời gian tối đa là {format_duration(MAX_DURATION_SECONDS)}."
        )
    return total_seconds


def parse_winner_count(value: str | int | None) -> int:
    if value is None:
        return 1
    if isinstance(value, int):
        winner_count = value
    else:
        raw = str(value).strip()
        if not raw:
            return 1
        if not raw.isdigit():
            raise GiveawayFormError(
                f"Số người thắng phải từ 1 đến {MAX_WINNERS}."
            )
        winner_count = int(raw)
    if winner_count < 1 or winner_count > MAX_WINNERS:
        raise GiveawayFormError(
            f"Số người thắng phải từ 1 đến {MAX_WINNERS}."
        )
    return winner_count


def parse_prize(value: str | None) -> str:
    prize = (value or "").strip()
    if not prize:
        raise GiveawayFormError("Hãy nhập phần thưởng.")
    if len(prize) > MAX_PRIZE_CHARS:
        raise GiveawayFormError(
            f"Phần thưởng tối đa {MAX_PRIZE_CHARS} ký tự."
        )
    return prize


def parse_giveaway_form(
    *,
    prize: str | None,
    duration: str | None,
    winners: str | int | None = None,
) -> GiveawayDraft:
    return GiveawayDraft(
        prize=parse_prize(prize),
        winner_count=parse_winner_count(winners),
        seconds=parse_duration(duration or ""),
    )


def parse_role_ids(values: object) -> tuple[int, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple, set)):
        items: list[object] = [values]
    else:
        items = list(values)

    ids: list[int] = []
    seen: set[int] = set()
    for item in items:
        raw = getattr(item, "id", item)
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            continue
        if role_id <= 0 or role_id in seen:
            continue
        seen.add(role_id)
        ids.append(role_id)
        if len(ids) >= MAX_GIVEAWAY_ROLES:
            break
    return tuple(ids)


def parse_bonus_multiplier(value: str | int | None) -> int:
    if value is None or (isinstance(value, str) and not str(value).strip()):
        return DEFAULT_BONUS_MULTIPLIER
    raw = str(value).strip().lower().rstrip("x×")
    if not raw.isdigit():
        raise GiveawayFormError(
            f"Hệ số thắng phải từ {MIN_BONUS_MULTIPLIER} đến {MAX_BONUS_MULTIPLIER}."
        )
    multiplier = int(raw)
    if multiplier < MIN_BONUS_MULTIPLIER or multiplier > MAX_BONUS_MULTIPLIER:
        raise GiveawayFormError(
            f"Hệ số thắng phải từ {MIN_BONUS_MULTIPLIER} đến {MAX_BONUS_MULTIPLIER}."
        )
    return multiplier


def settings_from_mapping(document: object | None) -> GiveawayRoleSettings:
    if not isinstance(document, dict):
        return GiveawayRoleSettings()
    try:
        multiplier = parse_bonus_multiplier(document.get("bonus_multiplier"))
    except GiveawayFormError:
        multiplier = DEFAULT_BONUS_MULTIPLIER
    return GiveawayRoleSettings(
        blacklist_role_ids=parse_role_ids(document.get("blacklist_role_ids")),
        bonus_role_ids=parse_role_ids(document.get("bonus_role_ids")),
        bonus_multiplier=multiplier,
    )


def member_role_ids(member: object | None) -> set[int]:
    roles = getattr(member, "roles", None)
    if not roles:
        return set()
    ids: set[int] = set()
    for role in roles:
        raw = getattr(role, "id", role)
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            continue
        if role_id > 0:
            ids.add(role_id)
    return ids


def is_blacklisted(role_ids: set[int], settings: GiveawayRoleSettings) -> bool:
    if not role_ids or not settings.blacklist_role_ids:
        return False
    return bool(role_ids.intersection(settings.blacklist_role_ids))


def entry_weight(role_ids: set[int], settings: GiveawayRoleSettings) -> int:
    if is_blacklisted(role_ids, settings):
        return 0
    if settings.bonus_role_ids and role_ids.intersection(settings.bonus_role_ids):
        return max(int(settings.bonus_multiplier), 1)
    return 1


def format_role_mentions(role_ids: tuple[int, ...] | list[int]) -> str:
    if not role_ids:
        return "_Không có._"
    mentions = [f"<@&{role_id}>" for role_id in role_ids]
    text = ", ".join(mentions)
    if len(text) <= MAX_ROLE_MENTION_CHARS:
        return text
    kept: list[str] = []
    used = 0
    suffix = "…"
    budget = MAX_ROLE_MENTION_CHARS - len(suffix)
    for mention in mentions:
        extra = len(mention) + (2 if kept else 0)
        if used + extra > budget:
            break
        kept.append(mention)
        used += extra
    return ", ".join(kept) + suffix if kept else suffix


def describe_role_settings(settings: GiveawayRoleSettings) -> str:
    blacklist = format_role_mentions(settings.blacklist_role_ids)
    if settings.bonus_role_ids:
        bonus = (
            f"{format_role_mentions(settings.bonus_role_ids)} "
            f"(x{settings.bonus_multiplier})"
        )
    else:
        bonus = "_Không có._"
    text = f"**Cấm tham gia:** {blacklist}\n**Tăng tỉ lệ:** {bonus}"
    if len(text) <= MAX_ROLE_MENTION_CHARS:
        return text
    return text[: MAX_ROLE_MENTION_CHARS - 1] + "…"


def pick_weighted_winners(
    entries: list[int],
    winner_count: int,
    weights: dict[int, int] | None = None,
) -> list[int]:
    unique = list(dict.fromkeys(int(entry) for entry in entries))
    if weights:
        unique = [uid for uid in unique if int(weights.get(uid, 1)) > 0]
    count = min(max(int(winner_count), 0), len(unique))
    if count <= 0:
        return []

    remaining = unique[:]
    winners: list[int] = []
    for _ in range(count):
        ticket_weights = []
        for uid in remaining:
            raw = 1 if not weights else weights.get(uid, 1)
            try:
                weight = int(raw)
            except (TypeError, ValueError):
                weight = 1
            ticket_weights.append(max(weight, 1))
        total = sum(ticket_weights)
        pick = random.uniform(0, total)
        upto = 0.0
        chosen = remaining[-1]
        for uid, weight in zip(remaining, ticket_weights):
            upto += weight
            if pick <= upto:
                chosen = uid
                break
        winners.append(chosen)
        remaining.remove(chosen)
    return winners
