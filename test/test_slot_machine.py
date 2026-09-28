import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from pymongo import ReturnDocument

from cogs.minigames.slot_machine._slot_helpers import (
    SLOT_COST,
    SLOT_JACKPOT,
    SLOT_PAIR,
    format_reels,
    slot_outcome_text,
    slot_payout,
    spin_reels,
)
from cogs.minigames.slot_machine.slot_machine import (
    NO_MENTIONS,
    SLOT_TABLE_FILENAME,
    SlotMachineCog,
    SlotMachineView,
)


class FakeAccounts:
    def __init__(self, documents=None):
        self.documents = {
            document["user_id"]: dict(document)
            for document in (documents or [])
        }

    def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
    ):
        if return_document is not ReturnDocument.AFTER:
            raise AssertionError("Balance mutations must return the new account")
        user_id = query["user_id"]
        document = self.documents.get(user_id)
        minimum = query.get("balance", {}).get("$gte")
        if document is None and not upsert:
            return None
        if document is not None and minimum is not None:
            if document.get("balance", 0) < minimum:
                return None
        if document is None:
            document = {"user_id": user_id}
            document.update(update.get("$setOnInsert", {}))
            self.documents[user_id] = document
        for key, amount in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + amount
        return dict(document)


class FakeTransactions:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(dict(document))


class FakeDatabase:
    def __init__(self, accounts, transactions):
        self.collections = {
            "user_accounts": accounts,
            "transaction_logs": transactions,
        }

    def __getitem__(self, name):
        return self.collections[name]


class TestSlotHelpers(unittest.TestCase):
    def test_seeded_spin_and_payouts(self) -> None:
        first = spin_reels(random.Random(7))
        second = spin_reels(random.Random(7))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)
        self.assertEqual(slot_payout(("seven", "seven", "seven")), SLOT_JACKPOT)
        self.assertEqual(slot_payout(("cherry", "cherry", "bar")), SLOT_PAIR)
        self.assertEqual(slot_payout(("cherry", "bell", "bar")), 0)
        self.assertEqual(format_reels(("seven", "diamond", "bar")), "7️⃣ | 💎 | BAR")
        self.assertEqual(slot_outcome_text(SLOT_JACKPOT), "NỔ HŨ!")
        with self.assertRaises(ValueError):
            slot_payout(("cherry", "bell"))
        with self.assertRaises(ValueError):
            slot_payout(("cherry", "bell", "unknown"))


class TestSlotEconomy(unittest.TestCase):
    def test_play_round_debits_and_credits_through_the_bank(self) -> None:
        accounts = FakeAccounts([{"user_id": 42, "balance": 20}])
        transactions = FakeTransactions()
        cog = SlotMachineCog(
            SimpleNamespace(db=FakeDatabase(accounts, transactions))
        )
        reels, payout, balance = cog.play_round(
            user_id=42,
            guild_id=7,
            session_id="slot-1",
            rng=random.Random(1),
        )
        self.assertEqual(len(reels), 3)
        self.assertEqual(payout, slot_payout(reels))
        expected = 20 - SLOT_COST + payout
        self.assertEqual(balance, expected)
        self.assertEqual(accounts.documents[42]["balance"], expected)
        self.assertEqual(transactions.documents[0]["type"], "slot_machine_play")
        if payout:
            self.assertEqual(transactions.documents[-1]["type"], "slot_machine_win")

    def test_play_round_returns_none_when_broke(self) -> None:
        cog = SlotMachineCog(
            SimpleNamespace(
                db=FakeDatabase(
                    FakeAccounts([{"user_id": 42, "balance": 4}]),
                    FakeTransactions(),
                )
            )
        )
        self.assertIsNone(
            cog.play_round(user_id=42, guild_id=None, session_id="slot-2")
        )


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
    )


class TestSlotView(unittest.IsolatedAsyncioTestCase):
    async def test_cabinet_is_owner_only_and_attaches_table(self) -> None:
        cog = SimpleNamespace(
            play_round=MagicMock(
                return_value=(("seven", "seven", "seven"), SLOT_JACKPOT, 195)
            ),
            unregister=MagicMock(),
        )
        view = SlotMachineView(
            cog,
            user_id=42,
            guild_id=None,
            display_name="Người chơi",
            session_id="slot-session",
            reels=("cherry", "bell", "bar"),
            payout=0,
            balance=95,
        )
        self.assertTrue(await view.interaction_check(make_interaction()))
        self.assertFalse(await view.interaction_check(make_interaction(99)))
        png = view._render_table()
        self.assertTrue(png.startswith(b"\x89PNG"))

        interaction = make_interaction()
        await view.spin_again_button.callback(interaction)
        cog.play_round.assert_called_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(view.payout, SLOT_JACKPOT)
        self.assertEqual(
            kwargs["embed"].image.url,
            f"attachment://{SLOT_TABLE_FILENAME}",
        )
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
        view.stop()

    async def test_replay_rejects_insufficient_balance(self) -> None:
        cog = SimpleNamespace(play_round=MagicMock(return_value=None), unregister=MagicMock())
        view = SlotMachineView(
            cog,
            user_id=42,
            guild_id=None,
            display_name="Người chơi",
            session_id="slot-session",
            reels=("cherry", "bell", "bar"),
            payout=0,
            balance=0,
        )
        interaction = make_interaction()
        await view.spin_again_button.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        self.assertEqual(view.reels, ("cherry", "bell", "bar"))
        view.stop()


if __name__ == "__main__":
    unittest.main()
