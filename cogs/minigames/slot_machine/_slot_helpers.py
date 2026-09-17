"""Pure slot-machine reel selection and payouts."""

from __future__ import annotations

import random
from typing import Sequence


SLOT_COST = 5
SLOT_JACKPOT = 100
SLOT_PAIR = 10
SLOT_GAME_NAME = "slot_machine"
SLOT_SYMBOLS = (
    "cherry",
    "bell",
    "lemon",
    "orange",
    "seven",
    "diamond",
    "bar",
)
SLOT_LABELS = {
    "cherry": "🍒",
    "bell": "🔔",
    "lemon": "🍋",
    "orange": "🍊",
    "seven": "7️⃣",
    "diamond": "💎",
    "bar": "BAR",
}


def spin_reels(rng: random.Random | None = None) -> tuple[str, str, str]:
    """Return three independent reel symbols."""

    picker = rng.choice if rng is not None else random.choice
    return (
        picker(SLOT_SYMBOLS),
        picker(SLOT_SYMBOLS),
        picker(SLOT_SYMBOLS),
    )


def slot_payout(reels: Sequence[str]) -> int:
    """Return the Trap Coin credit after the 5 TC stake was already deducted."""

    if len(reels) != 3:
        raise ValueError("A slot result must contain exactly three reels")
    if any(symbol not in SLOT_LABELS for symbol in reels):
        raise ValueError("Slot result contains an unknown symbol")
    first, second, third = reels
    if first == second == third:
        return SLOT_JACKPOT
    if first == second or second == third or first == third:
        return SLOT_PAIR
    return 0


def format_reels(reels: Sequence[str]) -> str:
    """Format reel symbols for Discord embed text."""

    return " | ".join(SLOT_LABELS[symbol] for symbol in reels)


def slot_outcome_text(payout: int) -> str:
    """Return the Vietnamese result banner for a settled spin."""

    if payout == SLOT_JACKPOT:
        return "NỔ HŨ!"
    if payout == SLOT_PAIR:
        return "Thắng cặp!"
    if payout == 0:
        return "Trượt mất rồi."
    raise ValueError("Unknown slot payout")
