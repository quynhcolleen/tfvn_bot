import random
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import discord
from pymongo import ReturnDocument

from cogs.utils._giveaway_helpers import (
    DEFAULT_BONUS_MULTIPLIER,
    MAX_DURATION_SECONDS,
    MAX_PRIZE_CHARS,
    MAX_WINNERS,
    MIN_DURATION_SECONDS,
    GiveawayCreateError,
    GiveawayFormError,
    GiveawayRoleSettings,
    can_manage_giveaway,
    describe_role_settings,
    entry_weight,
    format_duration,
    is_blacklisted,
    member_role_ids,
    parse_bonus_multiplier,
    parse_duration,
    parse_giveaway_form,
    parse_prize,
    parse_role_ids,
    parse_winner_count,
    pick_weighted_winners,
    settings_from_mapping,
)
from cogs.utils.giveaway import GiveawayCog, GiveawayView


class FakeCollection:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents: list[dict] = [dict(document) for document in documents or ()]
        self.insert_error: Exception | None = None
        self.write_error: Exception | None = None

    def create_index(self, *args, **kwargs) -> str:
        return str(kwargs.get("name", "index"))

    def insert_one(self, document: dict) -> SimpleNamespace:
        if self.insert_error is not None:
            raise self.insert_error
        self.documents.append(dict(document))
        return SimpleNamespace(inserted_id=document.get("message_id"))

    def find_one(self, query: dict) -> dict | None:
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def update_one(
        self,
        query: dict,
        update: dict,
        *,
        upsert: bool = False,
    ) -> SimpleNamespace:
        if self.write_error is not None:
            raise self.write_error
        selected = next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )
        inserted = selected is None and upsert
        if inserted:
            selected = {
                key: value
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self.documents.append(selected)
        if selected is not None:
            self._apply_update(selected, update)
        return SimpleNamespace(
            matched_count=int(selected is not None and not inserted),
            modified_count=int(selected is not None),
            upserted_id=("inserted" if inserted else None),
        )

    def find_one_and_update(
        self,
        query: dict,
        update: dict,
        return_document: Any = None,
    ) -> dict | None:
        if self.write_error is not None:
            raise self.write_error
        selected = next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )
        if selected is None:
            return None
        self._apply_update(selected, update)
        if return_document == ReturnDocument.BEFORE:
            return dict(selected)
        return dict(selected)

    @staticmethod
    def _apply_update(document: dict, update: dict) -> None:
        for key, value in update.get("$set", {}).items():
            current = document
            parts = str(key).split(".")
            for part in parts[:-1]:
                nested = current.get(part)
                if not isinstance(nested, dict):
                    nested = {}
                    current[part] = nested
                current = nested
            current[parts[-1]] = value
        for key, value in update.get("$addToSet", {}).items():
            items = document.setdefault(key, [])
            if value not in items:
                items.append(value)
        for key, value in update.get("$pull", {}).items():
            items = document.get(key)
            if isinstance(items, list) and value in items:
                items.remove(value)
        for key in update.get("$unset", {}):
            current = document
            parts = str(key).split(".")
            for part in parts[:-1]:
                nested = current.get(part)
                if not isinstance(nested, dict):
                    current = None
                    break
                current = nested
            if current is not None:
                current.pop(parts[-1], None)

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict):
                if "$nin" in expected:
                    if actual in expected["$nin"]:
                        return False
                    if isinstance(actual, list) and any(
                        item in actual for item in expected["$nin"]
                    ):
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
    def __init__(self, collection: FakeCollection | None = None) -> None:
        self._collections: dict[str, FakeCollection] = {}
        if collection is not None:
            self._collections["giveaways"] = collection

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


def make_permissions(*, administrator=False, manage_guild=False, manage_messages=False):
    return discord.Permissions(
        administrator=administrator,
        manage_guild=manage_guild,
        manage_messages=manage_messages,
    )


def make_join_interaction(
    *,
    user_id: int = 42,
    role_ids: tuple[int, ...] = (),
    bot_user: bool = False,
) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(
            id=user_id,
            bot=bot_user,
            roles=[SimpleNamespace(id=role_id) for role_id in role_ids],
        ),
        response=SimpleNamespace(
            defer=AsyncMock(side_effect=acknowledge),
            send_message=AsyncMock(side_effect=acknowledge),
            is_done=lambda: acknowledged["done"],
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class TestGiveawayHelpers(unittest.TestCase):
    def test_parse_duration_accepts_combined_units_and_bounds(self) -> None:
        self.assertEqual(parse_duration("10s"), 10)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration(" 2d "), 172800)
        self.assertEqual(parse_duration("30d"), MAX_DURATION_SECONDS)

    def test_parse_duration_rejects_invalid_and_out_of_range(self) -> None:
        for value in ("", "abc", "10", "0s", "9s", "31d", "40d"):
            with self.subTest(value=value):
                with self.assertRaises(GiveawayFormError):
                    parse_duration(value)

    def test_format_duration_uses_vietnamese_units(self) -> None:
        self.assertEqual(format_duration(0), "0 giây")
        self.assertEqual(format_duration(MIN_DURATION_SECONDS), "10 giây")
        self.assertEqual(format_duration(90), "1 phút 30 giây")
        self.assertEqual(format_duration(MAX_DURATION_SECONDS), "30 ngày")

    def test_parse_winner_count_defaults_and_bounds(self) -> None:
        self.assertEqual(parse_winner_count(None), 1)
        self.assertEqual(parse_winner_count(""), 1)
        self.assertEqual(parse_winner_count(" 3 "), 3)
        self.assertEqual(parse_winner_count(20), MAX_WINNERS)
        for value in ("0", "21", "abc", -1, 99):
            with self.subTest(value=value):
                with self.assertRaises(GiveawayFormError):
                    parse_winner_count(value)

    def test_parse_prize_strips_and_rejects_empty_or_too_long(self) -> None:
        self.assertEqual(parse_prize("  Nitro Classic  "), "Nitro Classic")
        with self.assertRaises(GiveawayFormError):
            parse_prize("   ")
        with self.assertRaises(GiveawayFormError):
            parse_prize("x" * (MAX_PRIZE_CHARS + 1))

    def test_parse_giveaway_form_builds_draft(self) -> None:
        draft = parse_giveaway_form(
            prize="  Discord Nitro  ",
            duration="1h",
            winners="3",
        )
        self.assertEqual(draft.prize, "Discord Nitro")
        self.assertEqual(draft.winner_count, 3)
        self.assertEqual(draft.seconds, 3600)

    def test_can_manage_giveaway_accepts_admin_or_mod_permissions(self) -> None:
        self.assertFalse(can_manage_giveaway(SimpleNamespace()))
        self.assertFalse(
            can_manage_giveaway(SimpleNamespace(guild_permissions=make_permissions()))
        )
        self.assertTrue(
            can_manage_giveaway(
                SimpleNamespace(guild_permissions=make_permissions(administrator=True))
            )
        )
        self.assertTrue(
            can_manage_giveaway(
                SimpleNamespace(guild_permissions=make_permissions(manage_guild=True))
            )
        )
        self.assertTrue(
            can_manage_giveaway(
                SimpleNamespace(guild_permissions=make_permissions(manage_messages=True))
            )
        )

    def test_parse_role_ids_and_bonus_multiplier(self) -> None:
        self.assertEqual(parse_role_ids(None), ())
        self.assertEqual(parse_role_ids([1, "2", 2, 0, "x"]), (1, 2))
        self.assertEqual(parse_role_ids(SimpleNamespace(id=9)), (9,))
        self.assertEqual(parse_bonus_multiplier(None), DEFAULT_BONUS_MULTIPLIER)
        self.assertEqual(parse_bonus_multiplier("3x"), 3)
        with self.assertRaises(GiveawayFormError):
            parse_bonus_multiplier("1")
        with self.assertRaises(GiveawayFormError):
            parse_bonus_multiplier("21")

    def test_blacklist_and_bonus_weight_rules(self) -> None:
        settings = GiveawayRoleSettings(
            blacklist_role_ids=(10,),
            bonus_role_ids=(20, 30),
            bonus_multiplier=5,
        )
        self.assertTrue(is_blacklisted({10, 20}, settings))
        self.assertFalse(is_blacklisted({20}, settings))
        self.assertEqual(entry_weight({10, 20}, settings), 0)
        self.assertEqual(entry_weight({20}, settings), 5)
        self.assertEqual(entry_weight({99}, settings), 1)
        self.assertEqual(
            member_role_ids(SimpleNamespace(roles=[SimpleNamespace(id=20)])),
            {20},
        )

    def test_settings_from_mapping_and_describe(self) -> None:
        settings = settings_from_mapping(
            {
                "blacklist_role_ids": ["11"],
                "bonus_role_ids": [22],
                "bonus_multiplier": 4,
            }
        )
        self.assertEqual(settings.blacklist_role_ids, (11,))
        self.assertEqual(settings.bonus_role_ids, (22,))
        self.assertEqual(settings.bonus_multiplier, 4)
        text = describe_role_settings(settings)
        self.assertIn("<@&11>", text)
        self.assertIn("<@&22>", text)
        self.assertIn("x4", text)
        self.assertLessEqual(len(text), 1024)

    def test_pick_weighted_winners_respects_weights_and_uniqueness(self) -> None:
        random.seed(0)
        winners = [
            pick_weighted_winners([1, 2], 1, {1: 1, 2: 1000})[0] for _ in range(80)
        ]
        self.assertGreater(winners.count(2), winners.count(1))
        unique = pick_weighted_winners([1, 2, 3], 3, {1: 5, 2: 5, 3: 5})
        self.assertEqual(sorted(unique), [1, 2, 3])
        self.assertEqual(pick_weighted_winners([1, 2], 1, {1: 0, 2: 3}), [2])


class TestGiveawayStart(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.collection = FakeCollection()
        self.bot = SimpleNamespace(
            db=FakeDatabase(self.collection),
            is_ready=lambda: False,
            add_view=Mock(),
            command_prefix="!tf ",
            get_guild=Mock(return_value=None),
        )
        self.cog = GiveawayCog(self.bot)
        self.addAsyncCleanup(self.cog.cog_unload)

    async def test_start_giveaway_persists_and_registers_join_view(self) -> None:
        sent = SimpleNamespace(id=555, jump_url="https://discord.com/channels/1/2/555")
        channel = SimpleNamespace(id=200, send=AsyncMock(return_value=sent))
        settings = GiveawayRoleSettings(
            blacklist_role_ids=(11,),
            bonus_role_ids=(22,),
            bonus_multiplier=3,
        )

        message, document = await self.cog.start_giveaway(
            guild_id=100,
            channel=channel,
            host_id=42,
            prize="Nitro Classic",
            winner_count=2,
            seconds=600,
            settings=settings,
        )

        self.assertIs(message, sent)
        self.assertEqual(document["message_id"], 555)
        self.assertEqual(document["prize"], "Nitro Classic")
        self.assertEqual(document["winner_count"], 2)
        self.assertEqual(document["host_id"], 42)
        self.assertEqual(document["guild_id"], 100)
        self.assertEqual(document["channel_id"], 200)
        self.assertEqual(document["blacklist_role_ids"], [11])
        self.assertEqual(document["bonus_role_ids"], [22])
        self.assertEqual(document["bonus_multiplier"], 3)
        self.assertFalse(document["ended"])
        self.assertEqual(document["entries"], [])
        self.assertEqual(len(self.collection.documents), 1)
        self.bot.add_view.assert_called_once()
        self.assertIn(555, self.cog.pending_giveaways)
        kwargs = channel.send.await_args.kwargs
        self.assertIn("Giveaway", kwargs["embed"].title)
        self.assertIn("<@&11>", str(kwargs["embed"].to_dict()))
        self.assertFalse(kwargs["allowed_mentions"].everyone)

    async def test_start_giveaway_deletes_message_when_persist_fails(self) -> None:
        sent = SimpleNamespace(id=556, delete=AsyncMock())
        channel = SimpleNamespace(id=200, send=AsyncMock(return_value=sent))
        self.collection.insert_error = RuntimeError("mongo down")

        with self.assertRaises(GiveawayCreateError):
            await self.cog.start_giveaway(
                guild_id=100,
                channel=channel,
                host_id=42,
                prize="Nitro",
                winner_count=1,
                seconds=60,
            )

        sent.delete.assert_awaited_once()
        self.assertFalse(self.collection.documents)
        self.assertNotIn(556, self.cog.pending_giveaways)

    async def test_cli_create_posts_giveaway_from_command_arguments(self) -> None:
        sent = SimpleNamespace(id=7, jump_url="https://discord.com/channels/100/200/7")
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            author=SimpleNamespace(
                id=42,
                guild_permissions=make_permissions(manage_messages=True),
            ),
            channel=SimpleNamespace(id=200, send=AsyncMock(return_value=sent)),
            clean_prefix="!tf ",
            reply=AsyncMock(),
        )

        await self.cog.giveaway.callback(
            self.cog,
            ctx,
            duration="10m",
            winner_or_prize="2",
            prize="Discord Nitro",
        )

        self.assertEqual(self.collection.documents[0]["prize"], "Discord Nitro")
        self.assertEqual(self.collection.documents[0]["winner_count"], 2)
        self.assertEqual(self.collection.documents[0]["message_id"], 7)
        confirmation = ctx.reply.await_args.args[0]
        self.assertIn("Discord Nitro", confirmation)
        self.assertIn("2 người thắng", confirmation)

    async def test_cli_create_uses_saved_guild_role_settings(self) -> None:
        self.cog.save_guild_settings(
            100,
            GiveawayRoleSettings(
                blacklist_role_ids=(5,),
                bonus_role_ids=(6,),
                bonus_multiplier=4,
            ),
            updated_by=42,
        )
        sent = SimpleNamespace(id=8, jump_url="https://discord.com/channels/100/200/8")
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            author=SimpleNamespace(
                id=42,
                guild_permissions=make_permissions(manage_messages=True),
            ),
            channel=SimpleNamespace(id=200, send=AsyncMock(return_value=sent)),
            clean_prefix="!tf ",
            reply=AsyncMock(),
        )

        await self.cog.giveaway.callback(
            self.cog,
            ctx,
            duration="10m",
            winner_or_prize="Nitro",
        )

        document = self.collection.documents[0]
        self.assertEqual(document["blacklist_role_ids"], [5])
        self.assertEqual(document["bonus_role_ids"], [6])
        self.assertEqual(document["bonus_multiplier"], 4)

    async def test_cli_create_rejects_invalid_duration_without_posting(self) -> None:
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            author=SimpleNamespace(
                id=42,
                guild_permissions=make_permissions(manage_messages=True),
            ),
            channel=SimpleNamespace(id=200, send=AsyncMock()),
            clean_prefix="!tf ",
            reply=AsyncMock(),
        )

        await self.cog.giveaway.callback(
            self.cog,
            ctx,
            duration="abc",
            winner_or_prize="Nitro",
        )

        ctx.channel.send.assert_not_awaited()
        self.assertFalse(self.collection.documents)
        self.assertIn("Thời gian không hợp lệ", ctx.reply.await_args.args[0])

    async def test_join_rejects_blacklisted_role_and_stores_bonus_weight(self) -> None:
        self.collection.documents.append(
            {
                "message_id": 9,
                "ended": False,
                "prize": "Nitro",
                "entries": [],
                "entry_meta": {},
                "blacklist_role_ids": [10],
                "bonus_role_ids": [20],
                "bonus_multiplier": 5,
            }
        )
        view = GiveawayView(self.cog, 9)
        self.addCleanup(view.stop)

        denied = make_join_interaction(role_ids=(10,))
        await self.cog.join_giveaway(denied, 9, view)
        self.assertIn("không được tham gia", denied.followup.send.await_args.args[0])
        self.assertEqual(self.collection.documents[0]["entries"], [])

        allowed = make_join_interaction(user_id=7, role_ids=(20,))
        await self.cog.join_giveaway(allowed, 9, view)
        self.assertEqual(self.collection.documents[0]["entries"], [7])
        self.assertEqual(self.collection.documents[0]["entry_meta"]["7"]["weight"], 5)

    async def test_end_giveaway_skips_blacklisted_members_and_uses_weights(self) -> None:
        self.collection.documents.append(
            {
                "message_id": 12,
                "channel_id": 200,
                "guild_id": 100,
                "ended": False,
                "prize": "Nitro",
                "winner_count": 1,
                "entries": [1, 2],
                "entry_meta": {
                    "1": {"weight": 1},
                    "2": {"weight": 50},
                },
                "blacklist_role_ids": [10],
                "bonus_role_ids": [20],
                "bonus_multiplier": 50,
            }
        )
        guild = SimpleNamespace(
            get_member=lambda user_id: (
                SimpleNamespace(id=1, roles=[SimpleNamespace(id=10)])
                if user_id == 1
                else SimpleNamespace(id=2, roles=[SimpleNamespace(id=20)])
            )
        )
        self.bot.get_guild.return_value = guild
        self.cog._edit_giveaway_message = AsyncMock()
        self.cog._announce_winners = AsyncMock()

        random.seed(1)
        ended = await self.cog.end_giveaway(12, announce=True)
        self.assertIsNotNone(ended)
        self.assertEqual(ended["winner_ids"], [2])
        self.assertTrue(ended["ended"])
