"""Pure Sic Bo (Tài/Xỉu) betting rules and payouts."""

from __future__ import annotations

import random
from enum import Enum
from typing import Sequence


SICBO_GAME_NAME = "sicbo"
TRIPLE_PAYOUT_MULTIPLIER = 31


class SicBoBet(str, Enum):
    BIG = "big"
    SMALL = "small"
    TRIPLE = "triple"


BET_LABELS = {
    SicBoBet.BIG: "Tài",
    SicBoBet.SMALL: "Xỉu",
    SicBoBet.TRIPLE: "Bộ ba",
}


def roll_dice(rng: random.Random | None = None) -> tuple[int, int, int]:
    """Roll three six-sided dice."""

    roller = rng.randint if rng is not None else random.randint
    return (roller(1, 6), roller(1, 6), roller(1, 6))


def is_triple(dice: Sequence[int]) -> bool:
    """Return whether all three dice show the same face."""

    _validate_dice(dice)
    return dice[0] == dice[1] == dice[2]


def winning_bet(dice: Sequence[int]) -> SicBoBet:
    """Return the unique winning market for a resolved roll.

    Any triple wins the triple market and loses Tài/Xỉu. Non-triple totals
    4–10 win Xỉu and 11–17 win Tài.
    """

    _validate_dice(dice)
    if is_triple(dice):
        return SicBoBet.TRIPLE
    total = sum(dice)
    if 4 <= total <= 10:
        return SicBoBet.SMALL
    if 11 <= total <= 17:
        return SicBoBet.BIG
    raise ValueError("Dice total is outside the Sic Bo range")


def payout_return(bet: int, choice: SicBoBet, dice: Sequence[int]) -> int:
    """Return the total credit due after a stake was already deducted."""

    if isinstance(bet, bool) or not isinstance(bet, int) or bet <= 0:
        raise ValueError("Sic Bo bet must be a positive integer")
    if choice not in BET_LABELS:
        raise ValueError("Unknown Sic Bo bet")
    if winning_bet(dice) is not choice:
        return 0
    if choice is SicBoBet.TRIPLE:
        return bet * TRIPLE_PAYOUT_MULTIPLIER
    return bet * 2


def format_dice(dice: Sequence[int]) -> str:
    """Format three dice for Discord embed text."""

    _validate_dice(dice)
    return " | ".join(str(int(value)) for value in dice)


def _validate_dice(dice: Sequence[int]) -> None:
    if len(dice) != 3:
        raise ValueError("Sic Bo uses exactly three dice")
    for value in dice:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6:
            raise ValueError("Each die must be an integer from 1 through 6")
