import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, mock_open, patch

import discord
from pymongo import ReturnDocument
from pymongo.errors import AutoReconnect

from cogs.daily_reward.user_account import TRANSACTION_LABELS
from cogs.minigames._card_game_economy import CardGameBank
from cogs.minigames.vietnamese_king import vietnamese_king
from cogs.minigames.word_connect import word_connect


class FakeContextCollection:
    def __init__(self, record, events):
        self.record = deepcopy(record)
        self.events = events
        self.error = None

    def find_one(self, query):
        if self.record and self.record["context_type"] == query["context_type"]:
            return deepcopy(self.record)
        return None

    def update_one(self, query, update, *, upsert=False):
        self.events.append("save")
        if self.error:
            raise self.error
        self.record = deepcopy(update["$set"])

    def delete_many(self, query):
        if self.error:
            raise self.error
        self.record = None


class FakeAccounts:
    def __init__(self, documents, events):
        self.documents = {doc["user_id"]: deepcopy(doc) for doc in documents}
        self.events = events
        self.calls = []
        self.error = None
        self.error_after_update = False

    def find_one_and_update(
        self, query, update, *, upsert=False, return_document=None
    ):
        self.events.append("credit")
        self.calls.append((deepcopy(query), deepcopy(update), upsert))
        if return_document is not ReturnDocument.AFTER:
            raise AssertionError("Credits must return the updated account")
        if self.error and not self.error_after_update:
            raise self.error
        user_id = query["user_id"]
        if user_id not in self.documents:
            if not upsert:
                return None
            self.documents[user_id] = deepcopy(update.get("$setOnInsert", {}))
        document = self.documents[user_id]
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        if self.error:
            raise self.error
        return deepcopy(document)


class FakeTransactions:
    def __init__(self, events):
        self.events = events
        self.documents = []
        self.error = None

    def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    def insert_one(self, document):
        self.events.append("audit")
        if self.error:
            raise self.error
        self.documents.append(deepcopy(document))


def make_word_game(game, *, documents=(), identical_next=False, add_cleanup=None):
    events = []
    accounts = FakeAccounts(documents, events)
    transactions = FakeTransactions(events)
    next_word = "hoa hồng" if identical_next else "mặt trời"
    if game == "vietnamese_king":
        record = {
            "context_type": game,
            "current_word": "hoa hồng",
            "current_standardized_word": "hoa hồng",
            "scrambled_letters": "G N Ồ H A O H",
            "revealed_indices": [],
        }
    else:
        record = {
            "context_type": game,
            "current_word": "bông hoa",
            "used_words": ["bông hoa"],
            "last_player_id": 13,
            "last_valid_message_id": 99,
        }
    context = FakeContextCollection(record, events)
    database = {
        "context": context,
        "user_accounts": accounts,
        "transaction_logs": transactions,
    }
    bot = SimpleNamespace(
        db=database,
        global_vars={
            "VIETNAMESE_KING_GAMES_CHANNELS": [7],
            "WORD_CONNECT_GAMES_CHANNELS": [7],
        },
        WORD_CONNECT_WORDS=[
            "bông hoa", "hoa hồng", "hoa lá", "lá sen", "cây cỏ", "cỏ cây"
        ],
        command_prefix="!tf ",
        all_commands={"vtv": object(), "noitu": object()},
        get_context=AsyncMock(return_value=SimpleNamespace(valid=False)),
    )
    if game == "vietnamese_king":
        dataset = [{"word": next_word, "standardize": next_word, "word_len": 7}]
        with patch.object(
            vietnamese_king, "open", mock_open(read_data=json.dumps(dataset)),
            create=True,
        ):
            cog = vietnamese_king.VietnameseKingCog(bot)
    else:
        cog = word_connect.WordConnectCommandCog(bot)
        random_word = patch.object(cog, "_random_word", return_value="cây cỏ")
        random_word.start()
        if add_cleanup is None:
            random_word.stop()
            raise AssertionError("Word Connect fixtures need addCleanup")
        add_cleanup(random_word.stop)
    return SimpleNamespace(
        game=game, cog=cog, bot=bot, accounts=accounts,
        transactions=transactions, context=context, events=events,
    )


def make_word_game_message(
    fixture, *, content="hoa hồng", user_id=42, message_id=101, channel_id=7, is_bot=False,
):
    async def send(*args, **kwargs):
        fixture.events.append("discord")
        return SimpleNamespace(delete=AsyncMock())

    return SimpleNamespace(
        id=message_id,
        created_at=discord.utils.utcnow(),
        content=content,
        author=SimpleNamespace(id=user_id, bot=is_bot, display_name="Người chơi"),
        guild=SimpleNamespace(id=8),
        channel=SimpleNamespace(id=channel_id, send=AsyncMock(side_effect=send)),
        add_reaction=AsyncMock(side_effect=send),
        reply=AsyncMock(side_effect=send),
    )


def make_word_game_command_context(message):
    return SimpleNamespace(
        channel=message.channel, author=message.author, guild=message.guild,
        message=message, send=message.channel.send,
    )


class TestWordGameRewards(unittest.IsolatedAsyncioTestCase):
    GAMES = ("vietnamese_king", "word_connect")
    REWARDS = {"vietnamese_king": 10, "word_connect": 50}

    def make_game(self, game, *, documents=(), identical_next=False):
        return make_word_game(
            game,
            documents=documents,
            identical_next=identical_next,
            add_cleanup=self.addCleanup,
        )

    def make_message(self, fixture, *, content="hoa hồng", user_id=42,
                     message_id=101, channel_id=7, is_bot=False):
        return make_word_game_message(
            fixture,
            content=content,
            user_id=user_id,
            message_id=message_id,
            channel_id=channel_id,
            is_bot=is_bot,
        )

    @staticmethod
    def make_command_context(message):
        return make_word_game_command_context(message)

    @staticmethod
    def output_text(message):
        calls = message.reply.await_args_list + message.channel.send.await_args_list
        return "\n".join(str(call.args[0]) for call in calls if call.args)

    async def reset(self, fixture, message):
        command = (
            fixture.cog.vtv_next if fixture.game == "vietnamese_king"
            else fixture.cog.wordconnect_end
        )
        await command.callback(fixture.cog, self.make_command_context(message))

    def assert_one_reward(self, fixture, message, initial_balance=0):
        reward = self.REWARDS[fixture.game]
        self.assertEqual(fixture.accounts.documents[42]["balance"], initial_balance + reward)
        self.assertEqual(len(fixture.accounts.calls), 1)
        self.assertEqual(len(fixture.transactions.documents), 1)
        record = fixture.transactions.documents[0]
        self.assertEqual(record["type"], f"{fixture.game}_win")
        self.assertEqual(
            TRANSACTION_LABELS[record["type"]],
            "Thắng Vua Tiếng Việt" if fixture.game == "vietnamese_king" else "Thắng Nối Từ",
        )
        self.assertEqual(record["user_id"], 42)
        self.assertEqual(record["guild_id"], 8)
        self.assertEqual(record["game_session_id"], str(message.id))
        self.assertEqual(record["transaction_type"], "credit")
        self.assertEqual(record["amount"], reward)
        self.assertEqual(record["balance_after"], initial_balance + reward)
        self.assertIn("timestamp", record)
        self.assertIn(f"{reward} TC", self.output_text(message))

    def assert_next_round_announced(self, fixture, message):
        key = "scrambled_letters" if fixture.game == "vietnamese_king" else "current_word"
        self.assertIn(fixture.context.record[key], self.output_text(message))

    async def test_winners_create_accounts_and_audit_before_discord_io(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                message = self.make_message(fixture)
                self.assertIsInstance(fixture.cog.bank, CardGameBank)

                await fixture.cog.on_message(message)

                self.assert_one_reward(fixture, message)
                self.assertEqual(fixture.events[:3], ["save", "credit", "audit"])
                self.assertEqual(fixture.events[3], "discord")
                self.assertNotEqual(fixture.context.record["current_word"], "hoa hồng")
                self.assertEqual(
                    fixture.context.record["current_word"], fixture.cog.current_word
                )

    async def test_wins_preserve_existing_account_fields(self):
        original = {
            "user_id": 42, "balance": 123,
            "cultivation": {"level": 7}, "daily_streak": 4,
        }
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game, documents=[original])
                message = self.make_message(fixture)

                await fixture.cog.on_message(message)

                self.assert_one_reward(fixture, message, initial_balance=123)
                expected = deepcopy(original)
                expected["balance"] += self.REWARDS[game]
                self.assertEqual(fixture.accounts.documents[42], expected)

    async def test_rules_embeds_show_reward_amounts(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                message = self.make_message(fixture)
                command = fixture.cog.vtv if game == "vietnamese_king" else fixture.cog.noitu

                await command.callback(fixture.cog, self.make_command_context(message))

                embed = message.channel.send.await_args_list[0].kwargs["embed"]
                self.assertIn(
                    f"{self.REWARDS[game]} TC", json.dumps(embed.to_dict(), ensure_ascii=False)
                )

    async def test_invalid_guesses_and_ignored_messages_never_credit(self):
        cases = (
            {"content": "không đúng"}, {"content": ""}, {"is_bot": True},
            {"channel_id": 999}, {"content": "!tf hoa hồng"},
        )
        for game in self.GAMES:
            for options in cases:
                with self.subTest(game=game, options=options):
                    fixture = self.make_game(game)
                    message = self.make_message(fixture, **options)
                    await fixture.cog.on_message(message)
                    self.assertEqual(fixture.accounts.calls, [])
                    self.assertEqual(fixture.transactions.documents, [])

    async def test_recognized_commands_never_credit_even_if_answer_matches(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                fixture.bot.get_context.return_value.valid = True
                await fixture.cog.on_message(self.make_message(fixture))
                self.assertEqual(fixture.accounts.calls, [])

    async def test_word_connect_ordinary_move_saves_without_reward(self):
        fixture = self.make_game("word_connect")
        message = self.make_message(fixture, content="hoa lá")

        await fixture.cog.on_message(message)

        self.assertEqual(fixture.accounts.calls, [])
        self.assertEqual(fixture.context.record["current_word"], "hoa lá")
        self.assertEqual(fixture.context.record["last_player_id"], 42)
        self.assertEqual(fixture.context.record["last_valid_message_id"], message.id)

    async def test_word_connect_rejected_moves_never_credit(self):
        for case in ("same_player", "already_used", "wrong_connection"):
            with self.subTest(case=case):
                fixture = self.make_game("word_connect")
                content = "hoa hồng"
                if case == "same_player":
                    fixture.cog.last_player_id = 42
                elif case == "already_used":
                    fixture.cog.used_words.append(content)
                else:
                    content = "lá sen"
                await fixture.cog.on_message(self.make_message(fixture, content=content))
                self.assertEqual(fixture.accounts.calls, [])

    async def test_word_connect_used_continuations_still_allow_dead_end_win(self):
        fixture = self.make_game("word_connect")
        fixture.cog.word_list.append("hồng hoa")
        fixture.cog.used_words.insert(0, "hồng hoa")
        message = self.make_message(fixture)

        await fixture.cog.on_message(message)

        self.assert_one_reward(fixture, message)

    async def test_hints_do_not_remove_reward_eligibility(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                message = self.make_message(fixture)
                command = (
                    fixture.cog.vtv_hint if game == "vietnamese_king"
                    else fixture.cog.word_connect_top
                )
                await command.callback(fixture.cog, self.make_command_context(message))
                self.assertEqual(fixture.accounts.calls, [])

                await fixture.cog.on_message(message)

                self.assert_one_reward(fixture, message)

    async def test_manual_resets_and_vtv_skips_never_credit(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                await self.reset(fixture, self.make_message(fixture))
                self.assertEqual(fixture.accounts.calls, [])
                self.assertEqual(fixture.transactions.documents, [])

    async def test_hint_exhaustion_advances_without_reward(self):
        fixture = self.make_game("vietnamese_king")
        indices = [i for i, char in enumerate(fixture.cog.current_word) if char != " "]
        fixture.cog.revealed_indices = indices[:-2]
        message = self.make_message(fixture)

        await fixture.cog.vtv_hint.callback(
            fixture.cog, self.make_command_context(message)
        )

        self.assertEqual(fixture.accounts.calls, [])
        self.assertEqual(fixture.context.record["current_word"], "mặt trời")
        self.assertIn("Không ai chiến thắng", self.output_text(message))

    async def test_unusable_vtv_dataset_closes_restored_round_and_pays_only_once(self):
        for dataset in ([], [{"word": "a", "word_len": 1}]):
            with self.subTest(dataset=dataset):
                fixture = self.make_game("vietnamese_king")
                fixture.cog.words_data = dataset
                message = self.make_message(fixture)

                await fixture.cog.on_message(message)
                await fixture.cog.on_message(self.make_message(fixture, message_id=102))

                self.assert_one_reward(fixture, message)
                self.assertIsNone(fixture.cog.current_word)
                self.assertIsNone(fixture.context.record["current_word"])

    async def test_word_connect_new_starter_ignores_previous_used_words(self):
        fixture = self.make_game("word_connect")
        fixture.cog._random_word = word_connect.WordConnectCommandCog._random_word.__get__(
            fixture.cog
        )
        fixture.cog.word_list = ["cây cỏ", "cỏ cây"]
        fixture.cog.used_words = ["cây cỏ", "cỏ cây"]

        await self.reset(fixture, self.make_message(fixture))

        self.assertIn(fixture.cog.current_word, fixture.cog.word_list)
        self.assertEqual(fixture.cog.used_words, [fixture.cog.current_word])
        self.assertFalse(fixture.cog._is_dead_end(fixture.cog.current_word))
        self.assertEqual(fixture.accounts.calls, [])

    async def test_simultaneous_winners_award_once_including_identical_vtv_puzzles(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game, identical_next=True)
                all_entered = asyncio.Event()
                release = asyncio.Event()
                entered = 0

                async def get_context(message):
                    nonlocal entered
                    entered += 1
                    if entered == 2:
                        all_entered.set()
                    await release.wait()
                    return SimpleNamespace(valid=False)

                fixture.bot.get_context.side_effect = get_context
                first = self.make_message(fixture)
                second = self.make_message(fixture, user_id=43, message_id=102)
                tasks = [
                    asyncio.create_task(fixture.cog.on_message(first)),
                    asyncio.create_task(fixture.cog.on_message(second)),
                ]
                try:
                    await asyncio.wait_for(all_entered.wait(), timeout=1)
                finally:
                    release.set()
                    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

                self.assertEqual(len(fixture.accounts.calls), 1)
                self.assertEqual(len(fixture.transactions.documents), 1)
                self.assertEqual(
                    sum(account["balance"] for account in fixture.accounts.documents.values()),
                    self.REWARDS[game],
                )

    async def test_queued_submissions_cannot_win_a_round_started_after_their_creation(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game, identical_next=True)
                if game == "word_connect":
                    fixture.cog._random_word.return_value = "bông hoa"
                first = self.make_message(fixture)
                second = self.make_message(fixture, user_id=43, message_id=102)

                async def reaction(*args, **kwargs):
                    await asyncio.sleep(0)

                first.add_reaction.side_effect = reaction
                second.add_reaction.side_effect = reaction

                await asyncio.wait_for(asyncio.gather(
                    fixture.cog.on_message(first), fixture.cog.on_message(second)
                ), timeout=1)

                self.assertEqual(len(fixture.accounts.calls), 1)
                self.assertEqual(len(fixture.transactions.documents), 1)
                self.assertEqual(fixture.accounts.documents[42]["balance"], self.REWARDS[game])

    async def test_new_submissions_can_win_the_next_round(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game, identical_next=True)
                if game == "word_connect":
                    fixture.cog._random_word.return_value = "bông hoa"
                await fixture.cog.on_message(self.make_message(fixture))

                second = self.make_message(fixture, message_id=102)
                await fixture.cog.on_message(second)

                self.assertEqual(len(fixture.accounts.calls), 2)
                self.assertEqual(fixture.accounts.documents[42]["balance"], 2 * self.REWARDS[game])
                self.assertEqual(
                    [record["game_session_id"] for record in fixture.transactions.documents],
                    ["101", "102"],
                )

    async def test_submissions_waiting_for_command_detection_cannot_win_reset_round(self):
        for game, action in (
            ("vietnamese_king", "reset"), ("word_connect", "reset"),
            ("vietnamese_king", "hint_exhaustion"),
        ):
            with self.subTest(game=game, action=action):
                fixture = self.make_game(game, identical_next=True)
                entered = asyncio.Event()
                release = asyncio.Event()

                async def get_context(message):
                    entered.set()
                    await release.wait()
                    return SimpleNamespace(valid=False)

                fixture.bot.get_context.side_effect = get_context
                message = self.make_message(fixture)
                task = asyncio.create_task(fixture.cog.on_message(message))
                try:
                    await asyncio.wait_for(entered.wait(), timeout=1)
                    if action == "reset":
                        await self.reset(fixture, message)
                    else:
                        indices = [
                            i for i, char in enumerate(fixture.cog.current_word)
                            if char != " "
                        ]
                        fixture.cog.revealed_indices = indices[:-2]
                        await fixture.cog.vtv_hint.callback(
                            fixture.cog, self.make_command_context(message)
                        )
                finally:
                    release.set()
                    await asyncio.wait_for(task, timeout=1)
                self.assertEqual(fixture.accounts.calls, [])

    async def test_context_failure_reports_failure_without_credit(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                original = deepcopy(fixture.context.record)
                fixture.context.error = AutoReconnect("round save unavailable")
                message = self.make_message(fixture)

                with self.assertLogs(level="ERROR"):
                    await fixture.cog.on_message(message)

                self.assertEqual(fixture.accounts.calls, [])
                self.assertEqual(fixture.transactions.documents, [])
                self.assertTrue(self.output_text(message))
                self.assertEqual(fixture.context.record, original)
                for key, value in original.items():
                    if key != "context_type":
                        self.assertEqual(getattr(fixture.cog, key), value)

    async def test_uncertain_credit_is_not_retried_and_next_round_is_saved(self):
        for game in self.GAMES:
            for applied in (False, True):
                with self.subTest(game=game, applied=applied):
                    fixture = self.make_game(game)
                    fixture.accounts.error = AutoReconnect("credit response unavailable")
                    fixture.accounts.error_after_update = applied
                    message = self.make_message(fixture)

                    with self.assertLogs(level="ERROR") as logs:
                        await fixture.cog.on_message(message)

                    self.assertEqual(len(fixture.accounts.calls), 1)
                    self.assertEqual(fixture.transactions.documents, [])
                    self.assertNotEqual(fixture.context.record["current_word"], "hoa hồng")
                    self.assert_next_round_announced(fixture, message)
                    self.assertIn("xác nhận", self.output_text(message))
                    self.assertNotIn("Đã cộng", self.output_text(message))
                    self.assertNotIn("Bạn nhận được", self.output_text(message))
                    self.assertIn(str(message.id), "\n".join(logs.output))
                    self.assertIn(str(message.author.id), "\n".join(logs.output))
                    if applied:
                        self.assertEqual(
                            fixture.accounts.documents[42]["balance"], self.REWARDS[game]
                        )
                    else:
                        self.assertEqual(fixture.accounts.documents, {})

    async def test_audit_failure_preserves_successful_payout_and_announcement(self):
        for game in self.GAMES:
            with self.subTest(game=game):
                fixture = self.make_game(game)
                fixture.transactions.error = AutoReconnect("audit unavailable")
                message = self.make_message(fixture)

                with self.assertLogs(level="ERROR"):
                    await fixture.cog.on_message(message)

                self.assertEqual(len(fixture.accounts.calls), 1)
                self.assertEqual(fixture.accounts.documents[42]["balance"], self.REWARDS[game])
                self.assertIn(f"{self.REWARDS[game]} TC", self.output_text(message))
                self.assert_next_round_announced(fixture, message)

    async def test_discord_failures_do_not_repeat_credit_or_suppress_next_round_send(self):
        for game in self.GAMES:
            for failure in ("reaction", "winner_notice", "next_notice"):
                with self.subTest(game=game, failure=failure):
                    fixture = self.make_game(game)
                    message = self.make_message(fixture)
                    error = discord.HTTPException(
                        SimpleNamespace(status=500, reason="test failure"), "delivery unavailable"
                    )
                    if failure == "reaction":
                        message.add_reaction.side_effect = error
                    elif failure == "winner_notice" and game == "vietnamese_king":
                        message.reply.side_effect = error
                    elif game == "word_connect":
                        if failure == "winner_notice":
                            message.channel.send.side_effect = [error, None]
                        else:
                            message.channel.send.side_effect = [None, error]
                    else:
                        message.channel.send.side_effect = error

                    with self.assertLogs(level="ERROR"):
                        await fixture.cog.on_message(message)

                    self.assertEqual(len(fixture.accounts.calls), 1)
                    self.assertEqual(fixture.accounts.documents[42]["balance"], self.REWARDS[game])
                    self.assert_next_round_announced(fixture, message)
                    self.assertEqual(
                        message.channel.send.await_count,
                        1 if game == "vietnamese_king" else 2,
                    )


if __name__ == "__main__":
    unittest.main()
