"""Win leaderboards for Vua Tiếng Việt and Nối Từ."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Iterable, Mapping, Sequence

import discord
from discord.ext import commands
from pymongo import ASCENDING
from pymongo.errors import PyMongoError


logger = logging.getLogger(__name__)

TRANSACTIONS_COLLECTION = "transaction_logs"
DEFAULT_LIMIT = 10
MAX_LIMIT = 10
INDEX_NAME = "word_game_win_leaderboard"
INDEX_KEYS = (("type", ASCENDING), ("transaction_type", ASCENDING))
CREDIT_TRANSACTION_TYPE = "credit"
NO_MENTIONS = discord.AllowedMentions.none()
RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
LEADERBOARD_FOOTER = "Toàn bộ lịch sử · Top {count} theo số lần thắng"
DATABASE_ERROR_MESSAGE = (
    "⚠️ Không thể tải bảng xếp hạng. Vui lòng thử lại sau."
)


@dataclass(frozen=True)
class WordGameLeaderboard:
    game: str
    event_type: str
    title: str
    empty: str
    color: int


@dataclass(frozen=True)
class WordGameRank:
    user_id: int
    wins: int
    tc: int
    last_win: datetime | None = None


LEADERBOARDS = {
    "vietnamese_king": WordGameLeaderboard(
        game="vietnamese_king",
        event_type="vietnamese_king_win",
        title="👑 BXH Vua Tiếng Việt",
        empty=(
            "Chưa có ai thắng Vua Tiếng Việt. "
            "Giải đúng câu đố để lên bảng xếp hạng."
        ),
        color=0xFFD700,
    ),
    "word_connect": WordGameLeaderboard(
        game="word_connect",
        event_type="word_connect_win",
        title="🏆 BXH Nối Từ",
        empty=(
            "Chưa có ai thắng Nối Từ. "
            "Nối vào ngõ cụt để lên bảng xếp hạng."
        ),
        color=0x2ECC71,
    ),
}


def leaderboard_for(game: str) -> WordGameLeaderboard:
    try:
        return LEADERBOARDS[game]
    except KeyError as error:
        raise ValueError(f"Unknown word-game leaderboard: {game}") from error


def clamp_leaderboard_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def leaderboard_pipeline(
    event_type: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    capped = clamp_leaderboard_limit(limit)
    return [
        {
            "$match": {
                "type": event_type,
                "transaction_type": CREDIT_TRANSACTION_TYPE,
            }
        },
        {
            "$group": {
                "_id": "$user_id",
                "wins": {"$sum": 1},
                "tc": {"$sum": "$amount"},
                "last_win": {"$max": "$timestamp"},
            }
        },
        {
            "$sort": {
                "wins": -1,
                "tc": -1,
                "last_win": 1,
                "_id": 1,
            }
        },
        {"$limit": capped},
    ]


def ensure_win_leaderboard_index(transactions: Any) -> None:
    try:
        transactions.create_index(list(INDEX_KEYS), name=INDEX_NAME)
    except PyMongoError:
        logger.exception("Failed to ensure word-game leaderboard index")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _rank_sort_key(rank: WordGameRank) -> tuple[int, int, datetime, int]:
    last_win = rank.last_win or datetime.max.replace(tzinfo=timezone.utc)
    return (-rank.wins, -rank.tc, last_win, rank.user_id)


def rank_win_documents(
    documents: Sequence[Mapping[str, Any]],
    *,
    event_type: str,
    limit: int = DEFAULT_LIMIT,
) -> tuple[WordGameRank, ...]:
    grouped: dict[int, WordGameRank] = {}
    for document in documents:
        if document.get("type") != event_type:
            continue
        if document.get("transaction_type") != CREDIT_TRANSACTION_TYPE:
            continue
        user_id = _as_int(document.get("user_id"))
        amount = _as_int(document.get("amount"))
        if user_id is None or amount is None or amount <= 0:
            continue
        last_win = _as_utc(document.get("timestamp"))
        current = grouped.get(user_id)
        if current is None:
            grouped[user_id] = WordGameRank(
                user_id=user_id,
                wins=1,
                tc=amount,
                last_win=last_win,
            )
            continue
        newer_last_win = current.last_win
        if last_win is not None and (
            newer_last_win is None or last_win > newer_last_win
        ):
            newer_last_win = last_win
        grouped[user_id] = WordGameRank(
            user_id=user_id,
            wins=current.wins + 1,
            tc=current.tc + amount,
            last_win=newer_last_win,
        )

    ordered = sorted(grouped.values(), key=_rank_sort_key)
    return tuple(ordered[: clamp_leaderboard_limit(limit)])


def ranks_from_aggregation(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[WordGameRank, ...]:
    ranks: list[WordGameRank] = []
    for row in rows:
        user_id = _as_int(row.get("_id"))
        wins = _as_int(row.get("wins"))
        tc = _as_int(row.get("tc"))
        if user_id is None or wins is None or wins <= 0 or tc is None or tc < 0:
            continue
        ranks.append(
            WordGameRank(
                user_id=user_id,
                wins=wins,
                tc=tc,
                last_win=_as_utc(row.get("last_win")),
            )
        )
    return tuple(ranks)


def fetch_win_ranks(
    transactions: Any,
    game: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> tuple[WordGameRank, ...]:
    spec = leaderboard_for(game)
    rows = transactions.aggregate(
        leaderboard_pipeline(spec.event_type, limit=limit)
    )
    return ranks_from_aggregation(rows)


def format_rank_lines(ranks: Sequence[WordGameRank]) -> tuple[str, ...]:
    lines: list[str] = []
    for index, rank in enumerate(ranks, start=1):
        medal = RANK_MEDALS.get(index, f"`#{index}`")
        lines.append(
            f"{medal} <@{rank.user_id}> — **{rank.wins}** lần · **{rank.tc:,}** TC"
        )
    return tuple(lines)


def build_leaderboard_embed(
    ranks: Sequence[WordGameRank],
    game: str,
) -> discord.Embed:
    spec = leaderboard_for(game)
    embed = discord.Embed(
        title=spec.title,
        description="\n".join(format_rank_lines(ranks)),
        color=spec.color,
    )
    embed.set_footer(text=LEADERBOARD_FOOTER.format(count=len(ranks)))
    return embed


async def send_win_leaderboard(
    ctx: commands.Context,
    transactions: Any,
    game: str,
) -> None:
    spec = leaderboard_for(game)
    try:
        ranks = fetch_win_ranks(transactions, game)
    except PyMongoError:
        logger.exception("Failed to load %s win leaderboard", game)
        await ctx.send(DATABASE_ERROR_MESSAGE, allowed_mentions=NO_MENTIONS)
        return
    if not ranks:
        await ctx.send(spec.empty, allowed_mentions=NO_MENTIONS)
        return
    await ctx.send(
        embed=build_leaderboard_embed(ranks, game),
        allowed_mentions=NO_MENTIONS,
    )
