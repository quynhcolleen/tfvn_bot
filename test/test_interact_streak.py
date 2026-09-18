from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import discord
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from cogs.interaction._interact_streak_helpers import (
    MILESTONE_DAYS,
    SOURCE_MENTION,
    SOURCE_REPLY,
    SOURCE_VOICE,
    STREAK_LIST_LIMIT,
    apply_streak_credit,
    displayed_current_streak,
    format_milestone_message,
    is_credit_eligible_voice_channel,
    is_live_streak,
    is_streak_milestone,
    live_dates,
    live_streak_query,
    normalize_pair,
    other_user_id,
    partners_from_message,
    previous_iso_date,
    source_label,
    vietnam_date,
    voice_overlap_partner_ids,
)
from cogs.interaction.interact_streak import (
    MILESTONE_MENTIONS,
    NO_MENTIONS,
    STREAKS_COLLECTION,
    InteractStreakCog,
)


GUILD_ID = 100
AUTHOR_ID = 10
PARTNER_ID = 20
OTHER_ID = 30
CHANNEL_ID = 200
AFK_CHANNEL_ID = 201
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-09-16"
YESTERDAY = "2026-09-15"


def make_user(user_id: int, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        bot=bot,
        mention=f"<@{user_id}>",
        display_name=f"User {user_id}",
    )


def make_member(
    user_id: int,
    *,
    guild: object | None = None,
    bot: bool = False,
    voice_channel: object | None = None,
) -> SimpleNamespace:
    member = make_user(user_id, bot=bot)
    member.guild = guild
    member.voice_channel = voice_channel
    return member


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = [dict(document) for document in documents]

    def sort(self, key_or_list: Any, direction: int | None = None) -> FakeCursor:
        if isinstance(key_or_list, str):
            keys = [(key_or_list, 1 if direction is None else direction)]
        else:
            keys = list(key_or_list)
        for key, order in reversed(keys):
            reverse = order in (-1, DESCENDING)
            self._documents.sort(
                key=lambda document, field=key: document.get(field) or 0,
                reverse=reverse,
            )
        return self

    def limit(self, count: int) -> FakeCursor:
        self._documents = self._documents[:count]
        return self

    def __iter__(self):
        return (dict(document) for document in self._documents)


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = [dict(document) for document in documents or ()]
        self.index_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.write_error: Exception | None = None
        self.find_error: Exception | None = None
        self.index_error: Exception | None = None
        self._next_id = 1

    def create_index(self, *args: Any, **kwargs: Any) -> str:
        if self.index_error is not None:
            raise self.index_error
        self.index_calls.append((args, kwargs))
        return str(kwargs.get("name", "index"))

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if self.find_error is not None:
            raise self.find_error
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def find(self, query: dict[str, Any]) -> FakeCursor:
        if self.find_error is not None:
            raise self.find_error
        return FakeCursor(
            [
                document
                for document in self.documents
                if self._matches(document, query)
            ]
        )

    def count_documents(self, query: dict[str, Any]) -> int:
        if self.find_error is not None:
            raise self.find_error
        return sum(
            1 for document in self.documents if self._matches(document, query)
        )

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: object = None,
    ) -> dict[str, Any] | None:
        if self.write_error is not None:
            raise self.write_error
        selected = next(
            (
                document
                for document in self.documents
                if self._matches(document, query)
            ),
            None,
        )
        payload = dict(update.get("$set") or {})
        if selected is None:
            if not upsert:
                return None
            inserted = {
                key: value
                for key, value in query.items()
                if key != "$or" and not isinstance(value, dict)
            }
            inserted.update(payload)
            if "_id" not in inserted:
                inserted["_id"] = self._next_id
                self._next_id += 1
            self._assert_unique(inserted, exclude=None)
            self.documents.append(inserted)
            return dict(inserted)

        proposed = dict(selected)
        proposed.update(payload)
        self._assert_unique(proposed, exclude=selected)
        selected.update(payload)
        return dict(selected)

    def _assert_unique(
        self,
        candidate: dict[str, Any],
        *,
        exclude: dict[str, Any] | None,
    ) -> None:
        for document in self.documents:
            if document is exclude:
                continue
            if (
                document.get("guild_id") == candidate.get("guild_id")
                and document.get("user_a") == candidate.get("user_a")
                and document.get("user_b") == candidate.get("user_b")
            ):
                raise DuplicateKeyError("duplicate pair")

    @staticmethod
    def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
        clauses = [
            {key: value}
            for key, value in query.items()
            if key != "$or"
        ]
        if any(
            not FakeCollection._match_clause(document, clause)
            for clause in clauses
        ):
            return False
        alternatives = query.get("$or")
        if not alternatives:
            return True
        return any(
            FakeCollection._match_clause(document, clause)
            for clause in alternatives
        )

    @staticmethod
    def _match_clause(document: dict[str, Any], clause: dict[str, Any]) -> bool:
        for key, expected in clause.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$gt" in expected and not (
                    actual is not None and actual > expected["$gt"]
                ):
                    return False
                if "$exists" in expected:
                    exists = key in document
                    if bool(expected["$exists"]) != exists:
                        return False
                continue
            if isinstance(actual, list) and not isinstance(expected, list):
                if expected not in actual:
                    return False
                continue
            if actual != expected:
                return False
        return True


class FakeDatabase:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection
        self.requested_names: list[str] = []

    def __getitem__(self, name: str) -> FakeCollection:
        self.requested_names.append(name)
        return self.collection


class StreakFixture:
    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
        *,
        voice_overlap_seconds: int = 0,
    ) -> None:
        self.collection = FakeCollection(documents)
        self.guild = SimpleNamespace(
            id=GUILD_ID,
            name="TFVN",
            afk_channel=SimpleNamespace(id=AFK_CHANNEL_ID),
            voice_channels=[],
            stage_channels=[],
            get_channel=lambda channel_id: next(
                (
                    channel
                    for channel in (
                        *self.guild.voice_channels,
                        *self.guild.stage_channels,
                    )
                    if channel.id == channel_id
                ),
                None,
            ),
        )
        self.bot = SimpleNamespace(
            db=FakeDatabase(self.collection),
            guilds=[self.guild],
            get_guild=lambda guild_id: self.guild if guild_id == GUILD_ID else None,
        )
        self.cog = InteractStreakCog(
            self.bot,
            voice_overlap_seconds=voice_overlap_seconds,
        )

    def make_context(self, *, author_id: int = AUTHOR_ID) -> SimpleNamespace:
        author = make_member(author_id, guild=self.guild)
        return SimpleNamespace(
            guild=self.guild,
            author=author,
            prefix="!tf ",
            send=AsyncMock(),
        )


def streak_document(
    *,
    user_a: int = AUTHOR_ID,
    user_b: int = PARTNER_ID,
    current_streak: int = 3,
    longest_streak: int = 5,
    last_active_date: str = TODAY,
    last_source: str = SOURCE_MENTION,
    guild_id: int = GUILD_ID,
) -> dict[str, Any]:
    low, high = sorted((user_a, user_b))
    return {
        "guild_id": guild_id,
        "user_a": low,
        "user_b": high,
        "partner_ids": [low, high],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "last_active_date": last_active_date,
        "last_source": last_source,
        "created_at": NOW.replace(tzinfo=None),
        "updated_at": NOW.replace(tzinfo=None),
    }


class TestStreakHelpers(unittest.TestCase):
    def test_normalize_pair_sorts_and_rejects_self(self) -> None:
        self.assertEqual(normalize_pair(20, 10), (10, 20))
        self.assertEqual(normalize_pair(10, 20), (10, 20))
        with self.assertRaises(ValueError):
            normalize_pair(10, 10)

    def test_vietnam_date_rolls_at_utc_17(self) -> None:
        before = datetime(2026, 9, 16, 16, 59, 59, tzinfo=timezone.utc)
        boundary = datetime(2026, 9, 16, 17, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(vietnam_date(before).isoformat(), "2026-09-16")
        self.assertEqual(vietnam_date(boundary).isoformat(), "2026-09-17")

    def test_live_window_is_today_and_yesterday(self) -> None:
        self.assertEqual(live_dates(TODAY), (TODAY, YESTERDAY))
        self.assertTrue(is_live_streak(TODAY, TODAY))
        self.assertTrue(is_live_streak(YESTERDAY, TODAY))
        self.assertFalse(is_live_streak("2026-09-14", TODAY))
        self.assertFalse(is_live_streak(None, TODAY))
        self.assertEqual(previous_iso_date(TODAY), YESTERDAY)

    def test_first_credit_starts_at_one(self) -> None:
        result = apply_streak_credit(
            None,
            guild_id=GUILD_ID,
            user_id_a=PARTNER_ID,
            user_id_b=AUTHOR_ID,
            today=TODAY,
            yesterday=YESTERDAY,
            source=SOURCE_MENTION,
            now=NOW,
        )
        self.assertTrue(result.advanced)
        self.assertEqual(result.document["user_a"], AUTHOR_ID)
        self.assertEqual(result.document["user_b"], PARTNER_ID)
        self.assertEqual(result.document["partner_ids"], [AUTHOR_ID, PARTNER_ID])
        self.assertEqual(result.document["current_streak"], 1)
        self.assertEqual(result.document["longest_streak"], 1)
        self.assertEqual(result.document["last_active_date"], TODAY)
        self.assertEqual(result.document["last_source"], SOURCE_MENTION)

    def test_same_day_is_noop_and_keeps_source(self) -> None:
        existing = streak_document(last_source=SOURCE_REPLY, current_streak=4)
        result = apply_streak_credit(
            existing,
            guild_id=GUILD_ID,
            user_id_a=AUTHOR_ID,
            user_id_b=PARTNER_ID,
            today=TODAY,
            yesterday=YESTERDAY,
            source=SOURCE_VOICE,
            now=NOW,
        )
        self.assertFalse(result.advanced)
        self.assertEqual(result.document["current_streak"], 4)
        self.assertEqual(result.document["last_source"], SOURCE_REPLY)

    def test_yesterday_increments_and_longest_grows(self) -> None:
        existing = streak_document(
            current_streak=4,
            longest_streak=4,
            last_active_date=YESTERDAY,
        )
        result = apply_streak_credit(
            existing,
            guild_id=GUILD_ID,
            user_id_a=AUTHOR_ID,
            user_id_b=PARTNER_ID,
            today=TODAY,
            yesterday=YESTERDAY,
            source=SOURCE_VOICE,
            now=NOW,
        )
        self.assertTrue(result.advanced)
        self.assertEqual(result.document["current_streak"], 5)
        self.assertEqual(result.document["longest_streak"], 5)
        self.assertEqual(result.document["last_source"], SOURCE_VOICE)

    def test_gap_resets_to_one_and_keeps_longest(self) -> None:
        existing = streak_document(
            current_streak=9,
            longest_streak=12,
            last_active_date="2026-09-10",
        )
        result = apply_streak_credit(
            existing,
            guild_id=GUILD_ID,
            user_id_a=AUTHOR_ID,
            user_id_b=PARTNER_ID,
            today=TODAY,
            yesterday=YESTERDAY,
            source=SOURCE_MENTION,
            now=NOW,
        )
        self.assertTrue(result.advanced)
        self.assertEqual(result.document["current_streak"], 1)
        self.assertEqual(result.document["longest_streak"], 12)

    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            apply_streak_credit(
                None,
                guild_id=GUILD_ID,
                user_id_a=AUTHOR_ID,
                user_id_b=PARTNER_ID,
                today=TODAY,
                yesterday=YESTERDAY,
                source="hug",
                now=NOW,
            )

    def test_displayed_current_is_zero_when_broken(self) -> None:
        live = streak_document(current_streak=7, last_active_date=YESTERDAY)
        broken = streak_document(current_streak=7, last_active_date="2026-09-01")
        self.assertEqual(displayed_current_streak(live, TODAY), 7)
        self.assertEqual(displayed_current_streak(broken, TODAY), 0)

    def test_live_query_and_other_user(self) -> None:
        query = live_streak_query(GUILD_ID, TODAY)
        self.assertEqual(query["guild_id"], GUILD_ID)
        self.assertEqual(query["last_active_date"]["$in"], [TODAY, YESTERDAY])
        self.assertEqual(query["current_streak"]["$gt"], 0)
        document = streak_document()
        self.assertEqual(other_user_id(document, AUTHOR_ID), PARTNER_ID)
        self.assertEqual(other_user_id(document, PARTNER_ID), AUTHOR_ID)
        with self.assertRaises(ValueError):
            other_user_id(document, OTHER_ID)

    def test_source_label(self) -> None:
        self.assertEqual(source_label(SOURCE_MENTION), "tin nhắn")
        self.assertEqual(source_label(SOURCE_REPLY), "trả lời")
        self.assertEqual(source_label(SOURCE_VOICE), "voice")
        self.assertEqual(source_label("nope"), "không rõ")

    def test_partners_from_mention_reply_and_skips(self) -> None:
        author = make_user(AUTHOR_ID)
        partner = make_user(PARTNER_ID)
        other = make_user(OTHER_ID)
        bot_user = make_user(99, bot=True)

        mentioned = partners_from_message(
            author_id=author.id,
            mentions=(partner, bot_user, author),
            reply_author=None,
        )
        self.assertEqual(
            [(item.user_id, item.source) for item in mentioned],
            [(PARTNER_ID, SOURCE_MENTION)],
        )

        replied = partners_from_message(
            author_id=author.id,
            mentions=(),
            reply_author=partner,
        )
        self.assertEqual(
            [(item.user_id, item.source) for item in replied],
            [(PARTNER_ID, SOURCE_REPLY)],
        )

        both = partners_from_message(
            author_id=author.id,
            mentions=(partner, other),
            reply_author=partner,
        )
        by_id = {item.user_id: item.source for item in both}
        self.assertEqual(by_id[PARTNER_ID], SOURCE_REPLY)
        self.assertEqual(by_id[OTHER_ID], SOURCE_MENTION)

        skip_bot_reply = partners_from_message(
            author_id=author.id,
            mentions=(),
            reply_author=bot_user,
        )
        self.assertEqual(skip_bot_reply, ())

    def test_voice_eligibility_and_occupants(self) -> None:
        self.assertFalse(is_credit_eligible_voice_channel(None, AFK_CHANNEL_ID))
        self.assertFalse(
            is_credit_eligible_voice_channel(AFK_CHANNEL_ID, AFK_CHANNEL_ID)
        )
        self.assertTrue(
            is_credit_eligible_voice_channel(CHANNEL_ID, AFK_CHANNEL_ID)
        )
        occupants = (
            make_user(AUTHOR_ID),
            make_user(PARTNER_ID),
            make_user(99, bot=True),
            make_user(AUTHOR_ID),
        )
        self.assertEqual(
            voice_overlap_partner_ids(AUTHOR_ID, occupants),
            (PARTNER_ID,),
        )
        self.assertEqual(voice_overlap_partner_ids(AUTHOR_ID, occupants[:1]), ())

    def test_milestone_days_ping_copy(self) -> None:
        self.assertEqual(MILESTONE_DAYS, frozenset({3, 7, 30, 100}))
        for days in (3, 7, 30, 100):
            self.assertTrue(is_streak_milestone(days))
        for days in (1, 2, 4, 6, 8, 29, 31, 99, 101):
            self.assertFalse(is_streak_milestone(days))
        text = format_milestone_message(AUTHOR_ID, PARTNER_ID, 7)
        self.assertIn(f"<@{AUTHOR_ID}>", text)
        self.assertIn(f"<@{PARTNER_ID}>", text)
        self.assertIn("**7 ngày**", text)


class TestInteractStreakCog(unittest.IsolatedAsyncioTestCase):
    def test_constructor_creates_indexes(self) -> None:
        fixture = StreakFixture()
        self.assertEqual(fixture.bot.db.requested_names, [STREAKS_COLLECTION])
        names = {kwargs["name"] for _, kwargs in fixture.collection.index_calls}
        self.assertEqual(
            names,
            {
                "guild_streak_pair_unique",
                "guild_streak_partners",
                "guild_streak_leaderboard",
            },
        )
        unique = next(
            kwargs
            for args, kwargs in fixture.collection.index_calls
            if kwargs.get("name") == "guild_streak_pair_unique"
        )
        self.assertTrue(unique["unique"])
        self.assertEqual(
            fixture.collection.index_calls[0][0][0],
            [
                ("guild_id", ASCENDING),
                ("user_a", ASCENDING),
                ("user_b", ASCENDING),
            ],
        )

    def test_constructor_logs_index_failure(self) -> None:
        collection = FakeCollection()
        collection.index_error = PyMongoError("offline")
        bot = SimpleNamespace(db=FakeDatabase(collection), guilds=[])
        cog = InteractStreakCog(bot)
        self.assertIs(cog.collection, collection)

    def test_credit_pair_inserts_and_is_idempotent_same_day(self) -> None:
        fixture = StreakFixture()
        first = fixture.cog.credit_pair(
            GUILD_ID,
            PARTNER_ID,
            AUTHOR_ID,
            SOURCE_MENTION,
            now=NOW,
        )
        self.assertIsNotNone(first)
        assert first is not None
        self.assertTrue(first.advanced)
        self.assertEqual(first.document["current_streak"], 1)
        self.assertEqual(len(fixture.collection.documents), 1)

        second = fixture.cog.credit_pair(
            GUILD_ID,
            AUTHOR_ID,
            PARTNER_ID,
            SOURCE_VOICE,
            now=NOW,
        )
        self.assertIsNone(second)
        self.assertEqual(fixture.collection.documents[0]["last_source"], SOURCE_MENTION)
        self.assertEqual(fixture.collection.documents[0]["current_streak"], 1)

    def test_credit_pair_continues_from_yesterday(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(
                    current_streak=2,
                    longest_streak=2,
                    last_active_date=YESTERDAY,
                )
            ]
        )
        updated = fixture.cog.credit_pair(
            GUILD_ID,
            AUTHOR_ID,
            PARTNER_ID,
            SOURCE_REPLY,
            now=NOW,
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertTrue(updated.advanced)
        self.assertEqual(updated.document["current_streak"], 3)
        self.assertEqual(updated.document["last_source"], SOURCE_REPLY)

    def test_credit_pair_ignores_self_and_unknown_source(self) -> None:
        fixture = StreakFixture()
        self.assertIsNone(
            fixture.cog.credit_pair(
                GUILD_ID, AUTHOR_ID, AUTHOR_ID, SOURCE_MENTION, now=NOW
            )
        )
        self.assertIsNone(
            fixture.cog.credit_pair(
                GUILD_ID, AUTHOR_ID, PARTNER_ID, "hug", now=NOW
            )
        )
        self.assertEqual(fixture.collection.documents, [])

    async def test_on_message_ignores_dm_bot_and_webhook(self) -> None:
        fixture = StreakFixture()
        author = make_user(AUTHOR_ID)
        partner = make_user(PARTNER_ID)
        dm = SimpleNamespace(
            guild=None,
            author=author,
            webhook_id=None,
            mentions=[partner],
            reference=None,
        )
        bot_msg = SimpleNamespace(
            guild=fixture.guild,
            author=make_user(AUTHOR_ID, bot=True),
            webhook_id=None,
            mentions=[partner],
            reference=None,
        )
        webhook = SimpleNamespace(
            guild=fixture.guild,
            author=author,
            webhook_id=55,
            mentions=[partner],
            reference=None,
        )
        await fixture.cog.on_message(dm)
        await fixture.cog.on_message(bot_msg)
        await fixture.cog.on_message(webhook)
        self.assertEqual(fixture.collection.documents, [])

    async def test_on_message_credits_mention_and_reply_partners(self) -> None:
        fixture = StreakFixture()
        author = make_user(AUTHOR_ID)
        partner = make_user(PARTNER_ID)
        other = make_user(OTHER_ID)
        message = SimpleNamespace(
            guild=fixture.guild,
            author=author,
            webhook_id=None,
            mentions=[partner, other],
            reference=SimpleNamespace(
                resolved=SimpleNamespace(author=partner)
            ),
        )
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_message(message)
        pairs = {
            (document["user_a"], document["user_b"]): document["last_source"]
            for document in fixture.collection.documents
        }
        self.assertEqual(pairs[(AUTHOR_ID, PARTNER_ID)], SOURCE_REPLY)
        self.assertEqual(pairs[(AUTHOR_ID, OTHER_ID)], SOURCE_MENTION)

    async def test_on_message_pings_both_on_milestone(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(
                    current_streak=2,
                    longest_streak=2,
                    last_active_date=YESTERDAY,
                )
            ]
        )
        channel = SimpleNamespace(id=CHANNEL_ID, send=AsyncMock())
        partner = make_user(PARTNER_ID)
        message = SimpleNamespace(
            guild=fixture.guild,
            author=make_user(AUTHOR_ID),
            webhook_id=None,
            mentions=[partner],
            reference=None,
            channel=channel,
        )
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_message(message)
        channel.send.assert_awaited_once()
        text, kwargs = channel.send.await_args.args[0], channel.send.await_args.kwargs
        self.assertEqual(
            text,
            format_milestone_message(AUTHOR_ID, PARTNER_ID, 3),
        )
        self.assertIs(kwargs["allowed_mentions"], MILESTONE_MENTIONS)
        self.assertTrue(MILESTONE_MENTIONS.users)

    async def test_on_message_does_not_ping_off_milestone(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(
                    current_streak=3,
                    longest_streak=3,
                    last_active_date=YESTERDAY,
                )
            ]
        )
        channel = SimpleNamespace(id=CHANNEL_ID, send=AsyncMock())
        message = SimpleNamespace(
            guild=fixture.guild,
            author=make_user(AUTHOR_ID),
            webhook_id=None,
            mentions=[make_user(PARTNER_ID)],
            reference=None,
            channel=channel,
        )
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_message(message)
        self.assertEqual(fixture.collection.documents[0]["current_streak"], 4)
        channel.send.assert_not_awaited()

    async def test_milestone_send_failure_is_swallowed(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(
                    current_streak=6,
                    longest_streak=6,
                    last_active_date=YESTERDAY,
                )
            ]
        )
        response = SimpleNamespace(status=403, reason="forbidden", headers={})
        channel = SimpleNamespace(
            id=CHANNEL_ID,
            send=AsyncMock(side_effect=discord.Forbidden(response, "forbidden")),
        )
        message = SimpleNamespace(
            guild=fixture.guild,
            author=make_user(AUTHOR_ID),
            webhook_id=None,
            mentions=[make_user(PARTNER_ID)],
            reference=None,
            channel=channel,
        )
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_message(message)
        self.assertEqual(fixture.collection.documents[0]["current_streak"], 7)
        channel.send.assert_awaited_once()

    async def test_voice_overlap_credits_after_wait(self) -> None:
        fixture = StreakFixture(voice_overlap_seconds=0)
        channel = SimpleNamespace(id=CHANNEL_ID, members=[], guild=fixture.guild)
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        fixture.guild.voice_channels = [channel]
        before = SimpleNamespace(channel=None)
        after = SimpleNamespace(channel=channel)
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_voice_state_update(author, before, after)
            await asyncio.sleep(0)
        stored = fixture.collection.find_one(
            {"guild_id": GUILD_ID, "user_a": AUTHOR_ID, "user_b": PARTNER_ID}
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["last_source"], SOURCE_VOICE)
        self.assertEqual(stored["current_streak"], 1)

    async def test_voice_milestone_pings_in_voice_channel(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(
                    current_streak=29,
                    longest_streak=29,
                    last_active_date=YESTERDAY,
                )
            ],
            voice_overlap_seconds=0,
        )
        channel = SimpleNamespace(
            id=CHANNEL_ID,
            members=[],
            guild=fixture.guild,
            send=AsyncMock(),
        )
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        fixture.guild.voice_channels = [channel]
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_voice_state_update(
                author,
                SimpleNamespace(channel=None),
                SimpleNamespace(channel=channel),
            )
            await asyncio.sleep(0)
        channel.send.assert_awaited_once_with(
            format_milestone_message(AUTHOR_ID, PARTNER_ID, 30),
            allowed_mentions=MILESTONE_MENTIONS,
        )

    async def test_voice_leave_cancels_pending_overlap(self) -> None:
        fixture = StreakFixture(voice_overlap_seconds=30)
        channel = SimpleNamespace(id=CHANNEL_ID, members=[], guild=fixture.guild)
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        fixture.guild.voice_channels = [channel]
        await fixture.cog.on_voice_state_update(
            author,
            SimpleNamespace(channel=None),
            SimpleNamespace(channel=channel),
        )
        self.assertEqual(len(fixture.cog._pending_voice), 1)
        channel.members = [partner]
        await fixture.cog.on_voice_state_update(
            author,
            SimpleNamespace(channel=channel),
            SimpleNamespace(channel=None),
        )
        await asyncio.sleep(0)
        self.assertEqual(fixture.cog._pending_voice, {})
        self.assertEqual(fixture.collection.documents, [])

    async def test_voice_skips_already_credited_pair(self) -> None:
        fixture = StreakFixture(
            [streak_document(last_active_date=TODAY)],
            voice_overlap_seconds=30,
        )
        channel = SimpleNamespace(id=CHANNEL_ID, members=[], guild=fixture.guild)
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_voice_state_update(
                author,
                SimpleNamespace(channel=None),
                SimpleNamespace(channel=channel),
            )
        self.assertEqual(fixture.cog._pending_voice, {})
        self.assertEqual(fixture.collection.documents[0]["current_streak"], 3)

    async def test_voice_skips_afk_channel(self) -> None:
        fixture = StreakFixture(voice_overlap_seconds=0)
        channel = SimpleNamespace(
            id=AFK_CHANNEL_ID, members=[], guild=fixture.guild
        )
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        await fixture.cog.on_voice_state_update(
            author,
            SimpleNamespace(channel=None),
            SimpleNamespace(channel=channel),
        )
        await asyncio.sleep(0)
        self.assertEqual(fixture.collection.documents, [])
        self.assertEqual(fixture.cog._pending_voice, {})

    async def test_on_ready_starts_missing_voice_timers(self) -> None:
        fixture = StreakFixture(voice_overlap_seconds=0)
        channel = SimpleNamespace(id=CHANNEL_ID, members=[], guild=fixture.guild)
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        fixture.guild.voice_channels = [channel]
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.on_ready()
            await asyncio.sleep(0)
        stored = fixture.collection.find_one(
            {"user_a": AUTHOR_ID, "user_b": PARTNER_ID}
        )
        self.assertIsNotNone(stored)

    async def test_cog_unload_cancels_pending_voice(self) -> None:
        fixture = StreakFixture(voice_overlap_seconds=60)
        channel = SimpleNamespace(id=CHANNEL_ID, members=[], guild=fixture.guild)
        author = make_member(AUTHOR_ID, guild=fixture.guild)
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        channel.members = [author, partner]
        fixture.cog._start_voice_overlaps(author, channel)
        self.assertEqual(len(fixture.cog._pending_voice), 1)
        fixture.cog.cog_unload()
        await asyncio.sleep(0)
        self.assertEqual(fixture.cog._pending_voice, {})

    async def test_streak_lists_live_partners(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(current_streak=4, longest_streak=8),
                streak_document(
                    user_b=OTHER_ID,
                    current_streak=2,
                    longest_streak=2,
                    last_active_date="2026-09-01",
                ),
            ]
        )
        ctx = fixture.make_context()
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.streak.callback(fixture.cog, ctx, None)
        ctx.send.assert_awaited_once()
        kwargs = ctx.send.await_args.kwargs
        embed = kwargs["embed"]
        self.assertIn(f"<@{PARTNER_ID}>", embed.description)
        self.assertNotIn(f"<@{OTHER_ID}>", embed.description)
        self.assertIn("**4** ngày", embed.description)
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)

    async def test_streak_empty_copy(self) -> None:
        fixture = StreakFixture()
        ctx = fixture.make_context()
        await fixture.cog.streak.callback(fixture.cog, ctx, None)
        message = ctx.send.await_args.args[0]
        self.assertIn("chưa có chuỗi đang sống", message.lower())

    async def test_streak_pair_and_self_and_bot(self) -> None:
        fixture = StreakFixture([streak_document(current_streak=6)])
        ctx = fixture.make_context()
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.streak.callback(fixture.cog, ctx, partner)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "🔥 Chuỗi tương tác")
        self.assertIn("**6** ngày", embed.fields[0].value)

        ctx.send.reset_mock()
        await fixture.cog.streak.callback(
            fixture.cog, ctx, make_member(AUTHOR_ID, guild=fixture.guild)
        )
        self.assertIn("member khác", ctx.send.await_args.args[0])

        ctx.send.reset_mock()
        await fixture.cog.streak.callback(
            fixture.cog, ctx, make_member(99, guild=fixture.guild, bot=True)
        )
        self.assertIn("bot", ctx.send.await_args.args[0].lower())

    async def test_streak_pair_missing_and_broken(self) -> None:
        fixture = StreakFixture()
        ctx = fixture.make_context()
        partner = make_member(PARTNER_ID, guild=fixture.guild)
        await fixture.cog.streak.callback(fixture.cog, ctx, partner)
        self.assertIn("Chưa có chuỗi", ctx.send.await_args.args[0])

        fixture.collection.documents.append(
            streak_document(
                last_active_date="2026-09-01",
                current_streak=11,
                longest_streak=11,
            )
        )
        ctx.send.reset_mock()
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.streak.callback(fixture.cog, ctx, partner)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertIn("đã đứt", embed.fields[0].value)
        self.assertIn("**11** ngày", embed.fields[1].value)

    async def test_streak_top_orders_live_pairs(self) -> None:
        fixture = StreakFixture(
            [
                streak_document(current_streak=2, longest_streak=2),
                streak_document(
                    user_b=OTHER_ID,
                    current_streak=9,
                    longest_streak=9,
                ),
                streak_document(
                    user_a=40,
                    user_b=41,
                    current_streak=50,
                    last_active_date="2026-09-01",
                ),
            ]
        )
        ctx = fixture.make_context()
        with patch("discord.utils.utcnow", return_value=NOW):
            await fixture.cog.streak_top.callback(fixture.cog, ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        description = embed.description
        self.assertLess(description.index(f"<@{OTHER_ID}>"), description.index(f"<@{PARTNER_ID}>"))
        self.assertNotIn("<@40>", description)
        self.assertEqual(STREAK_LIST_LIMIT, 10)

    async def test_streak_top_empty(self) -> None:
        fixture = StreakFixture()
        ctx = fixture.make_context()
        await fixture.cog.streak_top.callback(fixture.cog, ctx)
        self.assertIn("Chưa có chuỗi đang sống", ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
