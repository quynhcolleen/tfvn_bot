from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pymongo.errors import AutoReconnect

from cogs.minigames._word_game_leaderboard import (
    CREDIT_TRANSACTION_TYPE,
    DATABASE_ERROR_MESSAGE,
    DEFAULT_LIMIT,
    INDEX_KEYS,
    INDEX_NAME,
    LEADERBOARD_FOOTER,
    MAX_LIMIT,
    NO_MENTIONS,
    WordGameRank,
    build_leaderboard_embed,
    clamp_leaderboard_limit,
    ensure_win_leaderboard_index,
    fetch_win_ranks,
    format_rank_lines,
    leaderboard_for,
    leaderboard_pipeline,
    rank_win_documents,
    ranks_from_aggregation,
    send_win_leaderboard,
)
from test_word_game_rewards import (
    make_word_game,
    make_word_game_command_context,
    make_word_game_message,
)


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def win_document(
    user_id,
    *,
    game="vietnamese_king",
    amount=10,
    when=NOW,
    transaction_type=CREDIT_TRANSACTION_TYPE,
):
    return {
        "type": f"{game}_win",
        "transaction_type": transaction_type,
        "user_id": user_id,
        "amount": amount,
        "timestamp": when,
    }


class FakeLeaderboardTransactions:
    def __init__(self, rows=(), error=None, index_error=None):
        self.rows = list(rows)
        self.error = error
        self.index_error = index_error
        self.aggregate_calls = []
        self.create_index_calls = []

    def create_index(self, keys, **kwargs):
        self.create_index_calls.append((list(keys), dict(kwargs)))
        if self.index_error:
            raise self.index_error
        return kwargs.get("name")

    def aggregate(self, pipeline):
        self.aggregate_calls.append(pipeline)
        if self.error:
            raise self.error
        return list(self.rows)


class TestWordGameLeaderboardHelpers(unittest.TestCase):
    def test_leaderboard_specs_match_reward_event_types(self):
        self.assertEqual(
            leaderboard_for("vietnamese_king").event_type, "vietnamese_king_win"
        )
        self.assertEqual(leaderboard_for("word_connect").event_type, "word_connect_win")
        with self.assertRaises(ValueError):
            leaderboard_for("blackjack")

    def test_limit_clamps_to_the_public_top_ten(self):
        self.assertEqual(clamp_leaderboard_limit(0), 1)
        self.assertEqual(clamp_leaderboard_limit(3), 3)
        self.assertEqual(clamp_leaderboard_limit(99), MAX_LIMIT)
        self.assertEqual(clamp_leaderboard_limit(True), DEFAULT_LIMIT)
        self.assertEqual(clamp_leaderboard_limit("10"), DEFAULT_LIMIT)

    def test_pipeline_matches_credits_and_sorts_like_in_memory_ranking(self):
        pipeline = leaderboard_pipeline("vietnamese_king_win", limit=10)
        self.assertEqual(
            pipeline[0]["$match"],
            {
                "type": "vietnamese_king_win",
                "transaction_type": CREDIT_TRANSACTION_TYPE,
            },
        )
        self.assertEqual(pipeline[1]["$group"]["_id"], "$user_id")
        self.assertEqual(
            pipeline[2]["$sort"],
            {"wins": -1, "tc": -1, "last_win": 1, "_id": 1},
        )
        self.assertEqual(pipeline[3]["$limit"], 10)
        self.assertEqual(
            leaderboard_pipeline("word_connect_win", limit=99)[3]["$limit"],
            MAX_LIMIT,
        )

    def test_rank_win_documents_groups_and_ignores_other_records(self):
        documents = [
            win_document(1, amount=10, when=NOW),
            win_document(1, amount=10, when=NOW + timedelta(hours=1)),
            win_document(2, amount=10, when=NOW + timedelta(minutes=5)),
            win_document(1, game="word_connect", amount=50),
            win_document(3, transaction_type="debit"),
            win_document(True, amount=10),
            win_document(4, amount=0),
            {
                "type": "blackjack_win",
                "transaction_type": CREDIT_TRANSACTION_TYPE,
                "user_id": 9,
                "amount": 20,
                "timestamp": NOW,
            },
        ]

        ranks = rank_win_documents(documents, event_type="vietnamese_king_win")

        self.assertEqual(
            [(rank.user_id, rank.wins, rank.tc) for rank in ranks],
            [(1, 2, 20), (2, 1, 10)],
        )
        self.assertEqual(ranks[0].last_win, NOW + timedelta(hours=1))

    def test_sort_order_is_wins_then_tc_then_earlier_last_win_then_user_id(self):
        documents = [
            win_document(10, amount=10, when=NOW + timedelta(days=1)),
            win_document(11, amount=20, when=NOW + timedelta(days=2)),
            win_document(12, amount=10, when=NOW),
            win_document(13, amount=10, when=NOW),
            win_document(10, amount=10, when=NOW + timedelta(days=3)),
        ]

        ranks = rank_win_documents(documents, event_type="vietnamese_king_win")

        self.assertEqual(
            [rank.user_id for rank in ranks],
            [10, 11, 12, 13],
        )

    def test_naive_timestamps_compare_as_utc_and_limit_is_applied(self):
        naive = datetime(2026, 9, 11, 11, 0)
        documents = [
            win_document(index, when=NOW + timedelta(minutes=index))
            for index in range(1, 16)
        ]
        documents.append(win_document(1, when=naive))

        ranks = rank_win_documents(
            documents, event_type="vietnamese_king_win", limit=10
        )

        self.assertEqual(len(ranks), 10)
        self.assertEqual(ranks[0].user_id, 1)
        self.assertEqual(ranks[0].wins, 2)
        self.assertEqual(ranks[0].last_win, NOW + timedelta(minutes=1))
        self.assertEqual([rank.user_id for rank in ranks[1:]], list(range(2, 11)))

    def test_ranks_from_aggregation_skip_invalid_rows(self):
        ranks = ranks_from_aggregation(
            [
                {"_id": 7, "wins": 3, "tc": 30, "last_win": NOW},
                {"_id": True, "wins": 9, "tc": 90, "last_win": NOW},
                {"_id": 8, "wins": 0, "tc": 0, "last_win": NOW},
                {"_id": "nope", "wins": 2, "tc": 20},
            ]
        )
        self.assertEqual(ranks, (WordGameRank(7, 3, 30, NOW),))

    def test_format_lines_use_medals_and_do_not_ping_by_themselves(self):
        lines = format_rank_lines(
            (
                WordGameRank(11, 4, 40, NOW),
                WordGameRank(22, 2, 20, NOW),
                WordGameRank(33, 2, 20, NOW),
                WordGameRank(44, 1, 10, NOW),
            )
        )
        self.assertEqual(
            lines,
            (
                "🥇 <@11> — **4** lần · **40** TC",
                "🥈 <@22> — **2** lần · **20** TC",
                "🥉 <@33> — **2** lần · **20** TC",
                "`#4` <@44> — **1** lần · **10** TC",
            ),
        )

    def test_fetch_win_ranks_uses_the_game_pipeline(self):
        rows = [{"_id": 5, "wins": 2, "tc": 100, "last_win": NOW}]
        transactions = FakeLeaderboardTransactions(rows)

        ranks = fetch_win_ranks(transactions, "word_connect")

        self.assertEqual(len(transactions.aggregate_calls), 1)
        self.assertEqual(
            transactions.aggregate_calls[0][0]["$match"]["type"],
            "word_connect_win",
        )
        self.assertEqual(ranks[0], WordGameRank(5, 2, 100, NOW))

    def test_ensure_index_is_best_effort(self):
        transactions = FakeLeaderboardTransactions()
        ensure_win_leaderboard_index(transactions)
        self.assertEqual(
            transactions.create_index_calls,
            [(list(INDEX_KEYS), {"name": INDEX_NAME})],
        )

        failing = FakeLeaderboardTransactions(index_error=AutoReconnect("unavailable"))
        with self.assertLogs(
            "cogs.minigames._word_game_leaderboard", level="ERROR"
        ):
            ensure_win_leaderboard_index(failing)


class TestWordGameLeaderboardCommand(unittest.IsolatedAsyncioTestCase):
    def make_context(self):
        return SimpleNamespace(send=AsyncMock())

    async def test_empty_board_sends_the_game_specific_notice(self):
        ctx = self.make_context()
        transactions = FakeLeaderboardTransactions()

        await send_win_leaderboard(ctx, transactions, "vietnamese_king")

        ctx.send.assert_awaited_once_with(
            leaderboard_for("vietnamese_king").empty,
            allowed_mentions=NO_MENTIONS,
        )

    async def test_populated_board_sends_an_embed_without_mentions(self):
        ctx = self.make_context()
        transactions = FakeLeaderboardTransactions(
            [{"_id": 42, "wins": 3, "tc": 30, "last_win": NOW}]
        )

        await send_win_leaderboard(ctx, transactions, "vietnamese_king")

        kwargs = ctx.send.await_args.kwargs
        embed = kwargs["embed"]
        self.assertEqual(kwargs["allowed_mentions"], NO_MENTIONS)
        self.assertEqual(embed.title, "👑 BXH Vua Tiếng Việt")
        self.assertIn("<@42>", embed.description)
        self.assertIn("**3** lần", embed.description)
        self.assertEqual(embed.footer.text, LEADERBOARD_FOOTER.format(count=1))
        rebuilt = build_leaderboard_embed(
            (WordGameRank(42, 3, 30, NOW),), "vietnamese_king"
        )
        self.assertEqual(embed.to_dict(), rebuilt.to_dict())

    async def test_database_errors_are_reported_without_raising(self):
        ctx = self.make_context()
        transactions = FakeLeaderboardTransactions(error=AutoReconnect("down"))

        with self.assertLogs(
            "cogs.minigames._word_game_leaderboard", level="ERROR"
        ):
            await send_win_leaderboard(ctx, transactions, "word_connect")

        ctx.send.assert_awaited_once_with(
            DATABASE_ERROR_MESSAGE, allowed_mentions=NO_MENTIONS
        )


class TestWordGameLeaderboardCogs(unittest.IsolatedAsyncioTestCase):
    def make_game(self, game):
        return make_word_game(game, add_cleanup=self.addCleanup)

    async def test_top_commands_are_silent_outside_the_game_channel(self):
        for game, command_name in (
            ("vietnamese_king", "vtv_top"),
            ("word_connect", "noitu_leaderboard"),
        ):
            with self.subTest(game=game):
                fixture = self.make_game(game)
                message = make_word_game_message(fixture, channel_id=999)
                command = getattr(fixture.cog, command_name)

                await command.callback(
                    fixture.cog, make_word_game_command_context(message)
                )

                message.channel.send.assert_not_awaited()

    async def test_top_commands_render_ranked_wins_in_the_game_channel(self):
        cases = (
            ("vietnamese_king", "vtv_top", "vietnamese_king_win", 10),
            ("word_connect", "noitu_leaderboard", "word_connect_win", 50),
        )
        for game, command_name, event_type, amount in cases:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                fixture.transactions.aggregate = (
                    lambda pipeline, event=event_type, reward=amount: [
                        {
                            "_id": 42,
                            "wins": 2,
                            "tc": 2 * reward,
                            "last_win": NOW,
                        }
                    ]
                    if pipeline[0]["$match"]["type"] == event
                    else []
                )
                message = make_word_game_message(fixture)
                command = getattr(fixture.cog, command_name)

                await command.callback(
                    fixture.cog, make_word_game_command_context(message)
                )

                kwargs = message.channel.send.await_args.kwargs
                embed = kwargs["embed"]
                self.assertEqual(kwargs["allowed_mentions"], NO_MENTIONS)
                self.assertIn("<@42>", embed.description)
                self.assertIn("**2** lần", embed.description)
                self.assertIn(f"**{2 * amount:,}** TC", embed.description)

    async def test_rules_embeds_point_to_the_top_command(self):
        for game, command, needle in (
            ("vietnamese_king", "vtv", "vtv top"),
            ("word_connect", "noitu", "noitu top"),
        ):
            with self.subTest(game=game):
                fixture = self.make_game(game)
                message = make_word_game_message(fixture)
                callback = getattr(fixture.cog, command)

                await callback.callback(
                    fixture.cog, make_word_game_command_context(message)
                )

                embed = message.channel.send.await_args_list[0].kwargs["embed"]
                rendered = str(embed.to_dict())
                self.assertIn(needle, rendered)


if __name__ == "__main__":
    unittest.main()
