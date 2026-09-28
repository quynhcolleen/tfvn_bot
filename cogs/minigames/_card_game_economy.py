"""Shared Trap Coin accounting helpers for wager-based card games."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError


logger = logging.getLogger(__name__)

DEFAULT_BET = 5
MIN_BET = 5
MAX_BET = 1_000_000

# Backwards-friendly names for callers that describe the value as a wager.
DEFAULT_WAGER = DEFAULT_BET
MIN_WAGER = MIN_BET
MAX_WAGER = MAX_BET

ACCOUNTS_COLLECTION = "user_accounts"
TRANSACTIONS_COLLECTION = "transaction_logs"
VALID_CREDIT_REASONS = frozenset({"win", "push", "refund"})


_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def validate_wager(bet: int) -> str | None:
    """Return a Vietnamese validation error, or ``None`` for a valid bet."""

    if isinstance(bet, bool) or not isinstance(bet, int):
        return "Mức cược phải là một số nguyên."
    if bet < MIN_BET or bet > MAX_BET:
        return (
            f"Mức cược phải từ **{MIN_BET:,}** đến "
            f"**{MAX_BET:,} TC**."
        )
    return None


def parse_wager_input(raw: object) -> int:
    """Parse a typed wager, accepting optional thousands separators."""

    if not isinstance(raw, str):
        raise ValueError("Mức cược phải là một số nguyên.")
    text = raw.strip().replace("_", "").replace(" ", "")
    if _GROUPED_INTEGER.fullmatch(text):
        text = re.sub(r"[.,]", "", text)
    if not text.isdigit():
        raise ValueError("Mức cược phải là một số nguyên.")
    error = validate_wager(int(text))
    if error is not None:
        raise ValueError(error)
    return int(text)


class CardGameBank:
    """Apply card-game balance changes atomically and write audit records.

    Account mutations intentionally let :class:`~pymongo.errors.PyMongoError`
    escape so a cog can tell the player that settlement did not happen. Audit
    failures are logged instead: the completed balance mutation remains the
    source of truth and must not be repeated merely because logging failed.
    """

    def __init__(self, database: Any) -> None:
        self.accounts = database[ACCOUNTS_COLLECTION]
        self.transactions = database[TRANSACTIONS_COLLECTION]

    @staticmethod
    def _now():
        return discord.utils.utcnow()

    def _write_transaction(
        self,
        *,
        guild_id: int | None,
        user_id: int,
        transaction_type: str,
        event_type: str,
        amount: int,
        balance_after: int,
        session_id: str,
    ) -> None:
        try:
            self.transactions.insert_one(
                {
                    "guild_id": int(guild_id) if guild_id is not None else None,
                    "user_id": int(user_id),
                    "type": event_type,
                    "transaction_type": transaction_type,
                    "amount": int(amount),
                    "balance_after": int(balance_after),
                    "game_session_id": str(session_id),
                    "timestamp": self._now(),
                }
            )
        except PyMongoError:
            logger.exception(
                "Failed to write card-game transaction type=%s session=%s user=%s",
                event_type,
                session_id,
                user_id,
            )

    def reserve_wager(
        self,
        user_id: int,
        guild_id: int | None,
        game: str,
        bet: int,
        session_id: str,
    ) -> int | None:
        """Atomically debit ``bet`` and return the resulting balance.

        ``None`` means that no matching account had enough Trap Coin. Database
        failures propagate to the caller.
        """

        account = self.accounts.find_one_and_update(
            {"user_id": int(user_id), "balance": {"$gte": int(bet)}},
            {"$inc": {"balance": -int(bet)}},
            return_document=ReturnDocument.AFTER,
        )
        if account is None:
            return None

        balance_after = int(account.get("balance", 0))
        self._write_transaction(
            guild_id=guild_id,
            user_id=user_id,
            transaction_type="debit",
            event_type=f"{game}_play",
            amount=bet,
            balance_after=balance_after,
            session_id=session_id,
        )
        return balance_after

    def credit(
        self,
        user_id: int,
        guild_id: int | None,
        game: str,
        amount: int,
        session_id: str,
        reason: str,
    ) -> int:
        """Credit a win, push, or refund and return the resulting balance."""

        if reason not in VALID_CREDIT_REASONS:
            raise ValueError(f"Unknown card-game credit reason: {reason}")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("Card-game credit amount must be a positive integer")

        account = self.accounts.find_one_and_update(
            {"user_id": int(user_id)},
            {
                "$inc": {"balance": int(amount)},
                "$setOnInsert": {"user_id": int(user_id)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if account is None:  # Defensive: AFTER + upsert should always return it.
            raise PyMongoError("Credit completed without returning an account")

        balance_after = int(account.get("balance", amount))
        self._write_transaction(
            guild_id=guild_id,
            user_id=user_id,
            transaction_type="credit",
            event_type=f"{game}_{reason}",
            amount=amount,
            balance_after=balance_after,
            session_id=session_id,
        )
        return balance_after
