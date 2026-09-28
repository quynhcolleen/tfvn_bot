import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from discord.ext import commands
from pymongo.errors import PyMongoError

from cogs.settings._guild_variables import (
    ANNOUNCEMENT_VARIABLES,
    get_guild_variable,
    get_guild_variables,
)
from cogs.settings.variable_setting import VariableSetting
from scripts.migrate_guild_variables import migrate


class MemoryCollection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))
        self.create_index = Mock()

    @staticmethod
    def matches(row, query):
        return all(
            (key not in row if value == {"$exists": False} else row.get(key) == value)
            for key, value in query.items()
        )

    def find(self, query=None):
        return deepcopy([row for row in self.rows if self.matches(row, query or {})])

    def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if self.matches(row, query)), None)
        inserted = row is None
        if inserted:
            if not upsert:
                return SimpleNamespace(upserted_id=None)
            row = {key: deepcopy(value) for key, value in query.items()
                   if not isinstance(value, dict)}
            self.rows.append(row)
            row.update(deepcopy(update.get("$setOnInsert", {})))
        row.update(deepcopy(update.get("$set", {})))
        return SimpleNamespace(upserted_id=len(self.rows) if inserted else None)


def make_cog(rows=()):
    collection = MemoryCollection(rows)
    bot = SimpleNamespace(db={"global_variables": collection}, wait_for=AsyncMock())
    return VariableSetting(bot), collection


def context(guild_id=10, *, administrator=True):
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=guild_id), channel=SimpleNamespace(id=100),
        author=SimpleNamespace(id=1, guild_permissions=discord.Permissions(
            administrator=administrator
        )),
        permissions=discord.Permissions(administrator=administrator), send=AsyncMock(),
    )
    ctx.guild.get_member = Mock(return_value=ctx.author)
    return ctx


class TestAnnouncementVariableStore(unittest.TestCase):
    def test_same_name_is_independent_across_guilds_and_reloads(self):
        cog, collection = make_cog()
        cog.save_variable(10, "JOIN_CHANNEL", "101")
        cog.save_variable(20, "JOIN_CHANNEL", "202")
        cog.save_variable(10, "JOIN_CHANNEL", "103")
        reloaded = VariableSetting(cog.bot)
        self.assertEqual(get_guild_variable(reloaded.bot, 10, "JOIN_CHANNEL"), "103")
        self.assertEqual(get_guild_variable(reloaded.bot, 20, "JOIN_CHANNEL"), "202")
        self.assertIsNone(get_guild_variable(reloaded.bot, 30, "JOIN_CHANNEL"))
        self.assertEqual(len(collection.rows), 2)
        args, kwargs = collection.create_index.call_args
        self.assertEqual(args[0], [("guild_id", 1), ("name", 1)])
        self.assertTrue(kwargs["unique"])

    def test_original_configuration_remains_readable_without_guild_fallback(self):
        rows = [
            {"name": "BETA_ROLE_IDS", "value": ["shared"]},
            {"name": "JOIN_CHANNEL", "value": "legacy"},
            {"guild_id": 10, "name": "BETA_ROLE_IDS", "value": ["ignored"]},
            {"guild_id": 20, "name": "JOIN_CHANNEL", "value": "202"},
        ]
        cog, collection = make_cog(rows)
        self.assertEqual(cog.bot.global_vars, {"BETA_ROLE_IDS": ["shared"], "JOIN_CHANNEL": "legacy"})
        self.assertEqual(cog.bot.guild_vars, {20: {"JOIN_CHANNEL": "202"}})
        self.assertIsNone(get_guild_variable(cog.bot, 10, "JOIN_CHANNEL"))
        self.assertIsNone(get_guild_variable(cog.bot, 10, "BETA_ROLE_IDS"))
        self.assertEqual(collection.rows, rows)

    def test_unsupported_names_and_invalid_values_never_write(self):
        cog, collection = make_cog()
        for guild_id, name, value in (
            (10, "BETA_ROLE_IDS", "101"), (10, "BIRTHDAY_CHANNEL", "101"),
            (10, "JOIN_CHANNEL", ["101"]), (10, "JOIN_CHANNEL", " "),
            (True, "JOIN_CHANNEL", "101"), (0, "JOIN_CHANNEL", "101"),
        ):
            with self.subTest(name=name, guild_id=guild_id, value=value):
                with self.assertRaises(ValueError):
                    cog.save_variable(guild_id, name, value)
        self.assertEqual(collection.rows, [])

    def test_invalid_scopes_and_nonannouncement_cache_entries_are_ignored(self):
        bot = SimpleNamespace(guild_vars={10: {"BETA_ROLE_IDS": ["101"]}})
        for guild_id in (10, None, "10", True, -1):
            self.assertEqual(get_guild_variables(bot, guild_id), {})

    def test_database_failure_preserves_live_cache(self):
        cog, collection = make_cog()
        cog.save_variable(10, "JOIN_CHANNEL", "old")
        collection.update_one = Mock(side_effect=PyMongoError("offline"))
        with self.assertRaises(PyMongoError):
            cog.save_variable(10, "JOIN_CHANNEL", "new")
        self.assertEqual(get_guild_variable(cog.bot, 10, "JOIN_CHANNEL"), "old")


class TestAnnouncementVariableCommands(unittest.IsolatedAsyncioTestCase):
    async def test_value_prompt_is_scoped_and_announcement_change_is_immediate(self):
        cog, collection = make_cog()
        ctx = context()
        replies = [SimpleNamespace(content=value, author=ctx.author,
                                   channel=ctx.channel, guild=ctx.guild)
                   for value in (" ", " 101 ")]

        async def wait_for(event, *, check, timeout):
            self.assertEqual(timeout, 120)
            reply = replies.pop(0)
            self.assertTrue(check(reply))
            self.assertFalse(check(SimpleNamespace(
                author=ctx.author, channel=ctx.channel, guild=SimpleNamespace(id=20)
            )))
            return reply

        cog.bot.wait_for.side_effect = wait_for
        await cog.set_variable.callback(cog, ctx, "JOIN_CHANNEL")
        self.assertEqual(cog.bot.guild_vars, {10: {"JOIN_CHANNEL": "101"}})
        self.assertEqual(collection.rows[0]["type"], "STRING")

    async def test_nonannouncement_commands_rejected_even_for_bot_owner(self):
        cog, collection = make_cog([{"name": "BETA_ROLE_IDS", "value": ["private"]}])
        cog.bot.is_owner = AsyncMock(return_value=True)
        ctx = context()
        for name in ("BETA_ROLE_IDS", "HIGHLIGHT_CHANNEL", "KING_ROLE_ID", "arbitrary"):
            await cog.set_variable.callback(cog, ctx, name)
            await cog.get_variable.callback(cog, ctx, name)
        cog.bot.wait_for.assert_not_awaited()
        cog.bot.is_owner.assert_not_awaited()
        self.assertNotIn("private", str(ctx.send.call_args_list))
        self.assertEqual(collection.rows, [{"name": "BETA_ROLE_IDS", "value": ["private"]}])

    async def test_help_only_lists_announcement_keys(self):
        cog, _ = make_cog()
        ctx = context()
        await cog.setting.callback(cog, ctx)
        text = ctx.send.call_args.args[0]
        for name in ANNOUNCEMENT_VARIABLES:
            self.assertIn(name, text)
        self.assertNotIn("BETA_ROLE_IDS", text)

    async def test_cancel_timeout_and_revoked_permissions_do_not_write(self):
        for reply in ("cancel", None, "101"):
            cog, collection = make_cog()
            ctx = context(administrator=reply != "101")
            cog.bot.wait_for.side_effect = (
                asyncio.TimeoutError() if reply is None else [SimpleNamespace(content=reply)]
            )
            await cog.set_variable.callback(cog, ctx, "JOIN_CHANNEL")
            self.assertEqual(collection.rows, [])

    async def test_departed_admin_cannot_finish_old_prompt(self):
        cog, collection = make_cog()
        ctx = context()
        ctx.guild.get_member.return_value = None
        cog.bot.wait_for.return_value = SimpleNamespace(content="101")
        await cog.set_variable.callback(cog, ctx, "JOIN_CHANNEL")
        self.assertEqual(collection.rows, [])

    async def test_get_is_guild_scoped_and_bounds_messages_without_mentions(self):
        cog, _ = make_cog()
        cog.save_variable(10, "WELCOME_GIF_URL", "@everyone" + "x" * 4000)
        cog.save_variable(20, "WELCOME_GIF_URL", "other-server-value")
        ctx = context(10)
        await cog.get_variable.callback(cog, ctx, "WELCOME_GIF_URL")
        self.assertGreater(ctx.send.call_count, 1)
        for sent in ctx.send.call_args_list:
            self.assertLessEqual(len(sent.args[0]), 1900)
            self.assertNotIn("other-server-value", sent.args[0])
            self.assertFalse(sent.kwargs["allowed_mentions"].everyone)
        missing = context(30)
        await cog.get_variable.callback(cog, missing, "WELCOME_GIF_URL")
        self.assertIn("Không tìm thấy", missing.send.call_args.args[0])

    async def test_all_commands_require_guild_and_administrator(self):
        cog, _ = make_cog()
        for command in (cog.setting, cog.set_variable, cog.get_variable):
            ctx = context(administrator=False)
            with self.assertRaises(commands.MissingPermissions):
                for predicate in command.checks:
                    await discord.utils.maybe_coroutine(predicate, ctx)
            ctx = context()
            ctx.guild = None
            with self.assertRaises(commands.NoPrivateMessage):
                for predicate in command.checks:
                    await discord.utils.maybe_coroutine(predicate, ctx)


class TestAnnouncementVariableMigration(unittest.TestCase):
    def test_preview_apply_and_repeat_preserve_existing_and_unrelated_values(self):
        rows = [
            {"name": "JOIN_CHANNEL", "type": "STRING", "value": "legacy"},
            {"name": "BYE_CHANNEL", "type": "STRING", "value": "101"},
            {"guild_id": 10, "name": "JOIN_CHANNEL", "type": "STRING", "value": "custom"},
            {"guild_id": 20, "name": "JOIN_CHANNEL", "type": "STRING", "value": "other"},
            {"name": "BETA_ROLE_IDS", "type": "ARRAY", "value": ["201"]},
        ]
        collection = MemoryCollection(rows)
        self.assertEqual(migrate(collection, 10)["pending"], 1)
        self.assertEqual(collection.rows, rows)
        collection.create_index.assert_not_called()
        self.assertEqual(migrate(collection, 10, apply=True)["inserted"], 1)
        self.assertEqual(migrate(collection, 10, apply=True)["inserted"], 0)
        for row in rows:
            self.assertIn(row, collection.rows)
        self.assertEqual(collection.rows[-1], {
            "guild_id": 10, "name": "BYE_CHANNEL", "type": "STRING", "value": "101",
        })

    def test_invalid_or_conflicting_announcement_records_stop_before_writes(self):
        for rows in (
            [{"name": "JOIN_CHANNEL", "type": "ARRAY", "value": ["101"]}],
            [{"name": "JOIN_CHANNEL", "type": "STRING", "value": value} for value in ("a", "b")],
        ):
            collection = MemoryCollection(rows)
            with self.assertRaises(ValueError):
                migrate(collection, 10, apply=True)
            self.assertEqual(collection.rows, rows)
            collection.create_index.assert_not_called()
