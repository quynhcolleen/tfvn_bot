import unittest

from pymongo import ReturnDocument
from pymongo.errors import AutoReconnect

from cogs.minigames._card_game_economy import (
    MAX_BET,
    MIN_BET,
    CardGameBank,
    parse_wager_input,
    validate_wager,
)


class FakeAccounts:
    def __init__(self, documents=None):
        self.documents = {
            document["user_id"]: dict(document)
            for document in (documents or [])
        }
        self.calls = []

    def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
    ):
        self.calls.append((query, update, upsert, return_document))
        self.assert_after(return_document)
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

    @staticmethod
    def assert_after(return_document):
        if return_document is not ReturnDocument.AFTER:
            raise AssertionError("Balance mutations must return the new account")


class FakeTransactions:
    def __init__(self, error=None):
        self.documents = []
        self.error = error

    def insert_one(self, document):
        if self.error is not None:
            raise self.error
        self.documents.append(dict(document))


class FakeDatabase:
    def __init__(self, accounts, transactions):
        self.collections = {
            "user_accounts": accounts,
            "transaction_logs": transactions,
        }

    def __getitem__(self, name):
        return self.collections[name]


class TestCardGameEconomy(unittest.TestCase):
    def make_bank(self, balance=100, *, log_error=None):
        accounts = FakeAccounts([{"user_id": 42, "balance": balance}])
        transactions = FakeTransactions(log_error)
        return (
            CardGameBank(FakeDatabase(accounts, transactions)),
            accounts,
            transactions,
        )

    def test_wager_validation_includes_both_boundaries(self):
        self.assertIsNone(validate_wager(MIN_BET))
        self.assertIsNone(validate_wager(MAX_BET))
        self.assertIsNotNone(validate_wager(MIN_BET - 1))
        self.assertIsNotNone(validate_wager(MAX_BET + 1))
        self.assertIsNotNone(validate_wager(True))

    def test_wager_text_accepts_grouped_integers(self) -> None:
        self.assertEqual(parse_wager_input("50"), 50)
        self.assertEqual(parse_wager_input(" 1,000 "), 1000)
        self.assertEqual(parse_wager_input("1.000"), 1000)
        with self.assertRaises(ValueError):
            parse_wager_input("5.5")
        with self.assertRaises(ValueError):
            parse_wager_input("abc")
        with self.assertRaises(ValueError):
            parse_wager_input("1")
        with self.assertRaises(ValueError):
            parse_wager_input(5)

    def test_reserve_is_conditional_and_audited(self):
        bank, accounts, transactions = self.make_bank(balance=20)

        self.assertIsNone(bank.reserve_wager(42, 7, "blackjack", 25, "low"))
        self.assertEqual(accounts.documents[42]["balance"], 20)
        self.assertEqual(transactions.documents, [])

        self.assertEqual(
            bank.reserve_wager(42, 7, "blackjack", 5, "session-1"),
            15,
        )
        transaction = transactions.documents[0]
        self.assertEqual(transaction["guild_id"], 7)
        self.assertEqual(transaction["user_id"], 42)
        self.assertEqual(transaction["type"], "blackjack_play")
        self.assertEqual(transaction["transaction_type"], "debit")
        self.assertEqual(transaction["amount"], 5)
        self.assertEqual(transaction["balance_after"], 15)
        self.assertEqual(transaction["game_session_id"], "session-1")
        self.assertIn("timestamp", transaction)

    def test_credit_supports_win_push_and_refund_reasons(self):
        bank, accounts, transactions = self.make_bank(balance=10)

        self.assertEqual(bank.credit(42, None, "poker", 20, "one", "win"), 30)
        self.assertEqual(bank.credit(42, None, "poker", 5, "two", "push"), 35)
        self.assertEqual(
            bank.credit(42, None, "poker", 5, "three", "refund"),
            40,
        )
        self.assertEqual(accounts.documents[42]["balance"], 40)
        self.assertEqual(
            [document["type"] for document in transactions.documents],
            ["poker_win", "poker_push", "poker_refund"],
        )
        self.assertTrue(
            all(document["guild_id"] is None for document in transactions.documents)
        )

    def test_audit_failure_does_not_repeat_successful_balance_change(self):
        bank, accounts, _ = self.make_bank(
            balance=10,
            log_error=AutoReconnect("audit unavailable"),
        )

        self.assertEqual(bank.credit(42, 7, "blackjack", 5, "one", "refund"), 15)
        self.assertEqual(accounts.documents[42]["balance"], 15)


if __name__ == "__main__":
    unittest.main()
