import asyncio
import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
from discord.ext import commands
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from cogs._beta_function import BetaFunctionError
from cogs.operation._graceful_shutdown import ShutdownInProgress
from cogs.operation._setup_helpers import SetupCheck
from cogs.operation import operation_dashboard as dashboard_module
from cogs.operation._operation_helpers import (
    CSV_COLUMNS,
    MAX_ARGUMENT_LENGTH,
    MAX_EXPORT_ROWS,
    CsvPartTooLargeError,
    ExportRowLimitError,
    classify_command_error,
    command_error_type,
    get_audit_cutoff,
    get_prune_cutoff,
    sanitize_command_arguments,
    serialize_audit_csv,
    split_audit_csv,
)
from cogs.operation.operation_dashboard import (
    AuditLogView,
    BotOwnerGuildAdminView,
    DASHBOARD_TIMEOUT_SECONDS,
    DoctorView,
    ExportLogView,
    GuildAdminView,
    JOINED_SERVER_PAGE_SIZE,
    JoinedServerView,
    LifecycleHistoryView,
    OperationDashboardCog,
    OperationDashboardView,
    PruneLogView,
)
from cogs.operation.server_stats import ServerStatsCog


UTC = timezone.utc


class AtomicCollection:
    """Small Mongo update model used to exercise $setOnInsert ordering."""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.update_calls: list[tuple[dict, dict, bool]] = []

    def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        self.update_calls.append((query, update, upsert))
        event_id = query["_id"]
        inserted = event_id not in self.documents
        if inserted:
            if not upsert:
                return
            self.documents[event_id] = {}
            self.documents[event_id].update(update.get("$setOnInsert", {}))
        self.documents[event_id].update(update.get("$set", {}))


def make_cog(collection) -> OperationDashboardCog:
    cog = object.__new__(OperationDashboardCog)
    cog.bot = SimpleNamespace()
    cog.db = SimpleNamespace()
    cog.logs = collection
    cog.loaded_at = datetime(2026, 1, 1, tzinfo=UTC)
    return cog


def make_context(
    *,
    guild_id: int | None = 41,
    command_name: str | None = "moderation ban",
    message_id: int = 9001,
    content: str = "!tf moderation ban target https://secret.example/a  extra",
) -> SimpleNamespace:
    guild = None if guild_id is None else SimpleNamespace(id=guild_id)
    command = (
        None
        if command_name is None
        else SimpleNamespace(qualified_name=command_name)
    )
    message = SimpleNamespace(
        id=message_id,
        content=content,
        clean_content=content,
        created_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
    )
    return SimpleNamespace(
        guild=guild,
        command=command,
        message=message,
        channel=SimpleNamespace(id=501),
        author=SimpleNamespace(id=77, __str__=lambda self: "audit-admin"),
        prefix="!tf ",
        invoked_parents=("moderation",),
        invoked_with="ban",
    )


def make_interaction(
    *,
    guild_id: int | None = 41,
    user_id: int = 77,
    administrator: bool = True,
) -> SimpleNamespace:
    guild = (
        None
        if guild_id is None
        else SimpleNamespace(id=guild_id, filesize_limit=8 * 1024 * 1024)
    )
    response = SimpleNamespace(
        is_done=MagicMock(return_value=False),
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
        defer=AsyncMock(),
    )
    return SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        ),
        channel_id=501,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(),
    )


def make_cursor(documents: list[dict]) -> MagicMock:
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__iter__.return_value = iter(documents)
    return cursor


def make_guild(
    guild_id: int,
    name: str,
    *,
    member_count: int = 0,
    channel_count: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=guild_id,
        name=name,
        member_count=member_count,
        members=[object()] * member_count,
        channels=[object()] * channel_count,
        leave=AsyncMock(),
    )


def make_owner_cog(
    guilds: list[SimpleNamespace],
    *,
    owner: bool = True,
) -> SimpleNamespace:
    guild_map = {guild.id: guild for guild in guilds}
    bot = SimpleNamespace(
        guilds=guilds,
        is_owner=AsyncMock(return_value=owner),
        get_guild=MagicMock(side_effect=guild_map.get),
    )
    cog = object.__new__(OperationDashboardCog)
    cog.bot = bot
    cog.record_admin_action = AsyncMock(return_value=True)
    cog._guild_leave_in_flight = set()
    cog._departed_guild_ids = set()
    return cog


class TestOperationHelpers(unittest.TestCase):
    def test_sanitize_arguments_redacts_urls_normalizes_and_truncates(self) -> None:
        arguments = (
            "  first\nhttps://example.com/private?q=1   "
            "www.example.org/other\tdiscord.gg/private-code "
            "example.vn/token " + "x" * 700
        )

        result = sanitize_command_arguments(arguments)

        self.assertEqual(len(result), MAX_ARGUMENT_LENGTH)
        self.assertEqual(result.count("[url]"), 4)
        self.assertNotIn("example.com", result)
        self.assertNotIn("discord.gg", result)
        self.assertNotIn("\n", result)
        self.assertNotIn("\t", result)
        self.assertFalse(result.startswith(" "))
        self.assertEqual(sanitize_command_arguments(None), "")

    def test_error_classification_and_safe_type_unwrap(self) -> None:
        cooldown = commands.CommandOnCooldown(
            commands.Cooldown(1, 10),
            3.5,
            commands.BucketType.guild,
        )
        cases = (
            (cooldown, "cooldown"),
            (commands.MissingPermissions(["administrator"]), "denied"),
            (commands.DisabledCommand(), "denied"),
            (commands.BadArgument("bad input"), "invalid"),
            (commands.CommandNotFound("missing"), "invalid"),
            (commands.CommandInvokeError(ValueError("sensitive text")), "failed"),
        )

        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(classify_command_error(error), expected)

        wrapped = commands.CommandInvokeError(RuntimeError("do not persist me"))
        self.assertEqual(command_error_type(wrapped), "RuntimeError")
        self.assertNotIn("do not persist", command_error_type(wrapped))

    def test_audit_and_prune_ranges_have_exact_utc_boundaries(self) -> None:
        now = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)

        self.assertEqual(get_audit_cutoff("7d", now=now), now - timedelta(days=7))
        self.assertEqual(
            get_audit_cutoff("30d", now=now),
            now - timedelta(days=30),
        )
        self.assertEqual(
            get_prune_cutoff("180d", now=now),
            now - timedelta(days=180),
        )
        self.assertIsNone(get_audit_cutoff("all", now=now))
        self.assertIsNone(get_prune_cutoff("all", now=now))
        with self.assertRaises(ValueError):
            get_audit_cutoff("forever", now=now)
        with self.assertRaises(ValueError):
            get_prune_cutoff("7d", now=now)

    def test_csv_has_bom_stable_columns_escaping_and_formula_protection(self) -> None:
        created_at = datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC)
        document = {
            "created_at": created_at,
            "event_type": "command",
            "status": "succeeded",
            "command_name": "=HYPERLINK(\"bad\")",
            "arguments": "-1, \"quoted\"\nnext",
            "actor_id": 77,
            "actor_name": "@admin",
            "guild_id": 41,
            "details": {"formula": "+SUM(A1:A2)", "safe": True},
        }

        payload = serialize_audit_csv([document])

        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertEqual(rows[0]["created_at"], "2026-08-26T01:02:03Z")
        self.assertEqual(rows[0]["command_or_action"], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[0]["actor_name"], "'@admin")
        self.assertTrue(rows[0]["arguments"].startswith("'-1,"))
        self.assertIn("\nnext", rows[0]["arguments"])
        self.assertEqual(
            rows[0]["details"],
            '{"formula":"+SUM(A1:A2)","safe":true}',
        )

    def test_csv_split_produces_independent_bom_files_with_headers(self) -> None:
        document = {
            "created_at": datetime(2026, 8, 26, tzinfo=UTC),
            "event_type": "command",
            "status": "succeeded",
            "command_name": "ping",
            "guild_id": 41,
        }
        one_document = serialize_audit_csv([document])

        parts = split_audit_csv([document, document], max_bytes=len(one_document))

        self.assertEqual(len(parts), 2)
        for payload in parts:
            self.assertLessEqual(len(payload), len(one_document))
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["command_or_action"], "ping")

        with self.assertRaises(CsvPartTooLargeError):
            split_audit_csv([document], max_bytes=10)

    def test_csv_refuses_more_than_row_limit_without_silent_truncation(self) -> None:
        documents = ({} for _ in range(MAX_EXPORT_ROWS + 1))

        with self.assertRaises(ExportRowLimitError):
            serialize_audit_csv(documents)


class TestCommandAuditLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_group_fallback_does_not_drop_first_argument(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(
            command_name="afk",
            message_id=9000,
            content="!tf afk lunch break",
        )
        ctx.invoked_parents = ("afk",)
        ctx.invoked_with = "afk"

        await cog.on_command_completion(ctx)

        persisted = collection.documents["command:9000"]
        self.assertEqual(persisted["invoked_with"], "afk")
        self.assertEqual(persisted["arguments"], "lunch break")

    async def test_running_then_completion_persists_sanitized_command_fields(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context()

        await cog.on_command(ctx)
        running = collection.documents["command:9001"]
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["guild_id"], 41)
        self.assertEqual(running["channel_id"], 501)
        self.assertEqual(running["actor_id"], 77)
        self.assertEqual(running["command_name"], "moderation ban")
        self.assertEqual(running["invoked_with"], "moderation ban")
        self.assertEqual(running["arguments"], "target [url] extra")
        self.assertIsNone(running["completed_at"])

        await cog.on_command_completion(ctx)

        completed = collection.documents["command:9001"]
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsInstance(completed["completed_at"], datetime)
        self.assertIsNone(completed["error_type"])
        self.assertEqual(completed["created_at"], ctx.message.created_at)

    async def test_final_event_before_start_cannot_be_overwritten_by_running(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(message_id=9002)

        await cog.on_command_completion(ctx)
        await cog.on_command(ctx)

        persisted = collection.documents["command:9002"]
        self.assertEqual(persisted["status"], "succeeded")
        self.assertIsNotNone(persisted["completed_at"])
        first_update = collection.update_calls[0][1]
        delayed_start_update = collection.update_calls[1][1]
        self.assertEqual(first_update["$set"]["status"], "succeeded")
        self.assertNotIn("$set", delayed_start_update)
        self.assertEqual(
            delayed_start_update["$setOnInsert"]["status"],
            "running",
        )

    async def test_error_event_stores_category_and_class_not_exception_text(self) -> None:
        collection = AtomicCollection()
        cog = make_cog(collection)
        ctx = make_context(message_id=9003)
        error = commands.CommandInvokeError(RuntimeError("database password"))

        await cog.on_command_error(ctx, error)

        persisted = collection.documents["command:9003"]
        self.assertEqual(persisted["status"], "failed")
        self.assertEqual(persisted["error_type"], "RuntimeError")
        self.assertNotIn("database password", str(persisted))

    async def test_dm_and_unknown_command_events_are_ignored(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)

        await cog.on_command(make_context(guild_id=None))
        await cog.on_command_error(
            make_context(command_name=None),
            commands.CommandNotFound("not registered"),
        )

        collection.update_one.assert_not_called()

    async def test_pymongo_write_failure_never_escapes_command_listener(self) -> None:
        collection = MagicMock()
        collection.update_one.side_effect = PyMongoError("Mongo unavailable")
        cog = make_cog(collection)

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            await cog.on_command(make_context())
            await cog.on_command_completion(make_context())
            await cog.on_command_error(
                make_context(),
                commands.BadArgument("bad input"),
            )

        self.assertEqual(collection.update_one.call_count, 3)
        self.assertEqual(log_failure.call_count, 3)


class TestGuildScopedMongoOperations(unittest.IsolatedAsyncioTestCase):
    def test_index_matches_guild_time_and_deterministic_sort(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)

        self.assertTrue(cog._ensure_indexes())

        collection.create_index.assert_called_once_with(
            [
                ("guild_id", ASCENDING),
                ("created_at", DESCENDING),
                ("_id", DESCENDING),
            ],
            name="guild_created_at_id",
        )

    async def test_page_and_export_queries_are_guild_scoped_and_newest_first(self) -> None:
        collection = MagicMock()
        page_cursor = make_cursor([{"_id": "page"}])
        export_cursor = make_cursor([{"_id": "export"}])
        collection.find.side_effect = [page_cursor, export_cursor]
        collection.count_documents.return_value = 1
        cog = make_cog(collection)

        documents, total = await cog.fetch_audit_page(
            guild_id=41,
            range_key="30d",
            offset=-10,
            limit=999,
        )
        exported = await cog.fetch_export_documents(
            guild_id=52,
            range_key="all",
        )

        self.assertEqual(documents, [{"_id": "page"}])
        self.assertEqual(total, 1)
        self.assertEqual(exported, [{"_id": "export"}])
        page_query = collection.find.call_args_list[0].args[0]
        export_query = collection.find.call_args_list[1].args[0]
        self.assertEqual(page_query["guild_id"], 41)
        self.assertEqual(set(page_query), {"guild_id", "created_at"})
        self.assertIn("$gte", page_query["created_at"])
        self.assertEqual(export_query, {"guild_id": 52})
        page_cursor.sort.assert_called_once_with(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        )
        page_cursor.skip.assert_called_once_with(0)
        page_cursor.limit.assert_called_once_with(dashboard_module.AUDIT_PAGE_SIZE)
        export_cursor.sort.assert_called_once_with(
            [("created_at", DESCENDING), ("_id", DESCENDING)]
        )
        export_cursor.limit.assert_called_once_with(MAX_EXPORT_ROWS + 1)
        count_query = collection.count_documents.call_args.args[0]
        self.assertEqual(count_query, page_query)

    async def test_export_query_detects_row_limit(self) -> None:
        collection = MagicMock()
        cursor = make_cursor([{}, {}, {}])
        collection.find.return_value = cursor
        cog = make_cog(collection)

        with patch.object(dashboard_module, "MAX_EXPORT_ROWS", 2):
            with self.assertRaises(ExportRowLimitError):
                await cog.fetch_export_documents(guild_id=41, range_key="all")

        cursor.limit.assert_called_once_with(3)
        self.assertEqual(collection.find.call_args.args[0], {"guild_id": 41})

    async def test_count_and_prune_filters_never_cross_guilds(self) -> None:
        collection = MagicMock()
        collection.count_documents.return_value = 8
        collection.delete_many.return_value = SimpleNamespace(deleted_count=7)
        cog = make_cog(collection)
        cutoff = datetime(2026, 5, 1, tzinfo=UTC)

        count = await cog.count_prunable_logs(guild_id=41, cutoff=cutoff)
        deleted = await cog.prune_logs(guild_id=52, cutoff=None)

        self.assertEqual(count, 8)
        self.assertEqual(deleted, 7)
        collection.count_documents.assert_called_once_with(
            {"guild_id": 41, "created_at": {"$lt": cutoff}}
        )
        collection.delete_many.assert_called_once_with({"guild_id": 52})

    async def test_admin_action_is_written_to_interaction_guild(self) -> None:
        collection = MagicMock()
        cog = make_cog(collection)
        interaction = make_interaction(guild_id=73)

        persisted = await cog.record_admin_action(
            interaction=interaction,
            action="export_logs",
            status="succeeded",
            details={"rows": 4},
        )

        self.assertTrue(persisted)
        document = collection.insert_one.call_args.args[0]
        self.assertEqual(document["guild_id"], 73)
        self.assertEqual(document["event_type"], "admin_action")
        self.assertEqual(document["action"], "export_logs")
        self.assertEqual(document["details"], {"rows": 4})

    async def test_admin_action_failure_is_best_effort(self) -> None:
        collection = MagicMock()
        collection.insert_one.side_effect = PyMongoError("Mongo unavailable")
        cog = make_cog(collection)

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            persisted = await cog.record_admin_action(
                interaction=make_interaction(guild_id=73),
                action="prune_logs",
                status="failed",
                details={"reason": "database"},
            )

        self.assertFalse(persisted)
        log_failure.assert_called_once()


class TestDashboardSnapshot(unittest.IsolatedAsyncioTestCase):
    def make_dashboard_cog(self):
        current_guild = SimpleNamespace(
            id=41,
            member_count=20,
            members=[object()] * 4,
            channels=[object(), object(), object()],
        )
        other_guild = SimpleNamespace(
            id=52,
            member_count=7,
            members=[object()] * 2,
            channels=[],
        )
        started_at = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
        bot = SimpleNamespace(
            guilds=[current_guild, other_guild],
            latency=0.1236,
            environment="development",
            is_ready=MagicMock(return_value=True),
            get_cog=MagicMock(
                return_value=SimpleNamespace(start_time=started_at)
            ),
        )
        logs = MagicMock()
        logs.count_documents.return_value = 321
        logs.aggregate.return_value = [
            {"_id": "succeeded", "count": 8},
            {"_id": "failed", "count": 2},
        ]
        cog = make_cog(logs)
        cog.bot = bot
        cog.db = MagicMock()
        return cog, current_guild, started_at

    async def test_healthy_snapshot_and_embed_render_runtime_and_mongo_counts(
        self,
    ) -> None:
        cog, guild, started_at = self.make_dashboard_cog()
        now = datetime(2026, 8, 26, 12, 30, tzinfo=UTC)
        with (
            patch.object(dashboard_module.discord.utils, "utcnow", return_value=now),
            patch.object(
                dashboard_module.time,
                "perf_counter",
                side_effect=(10.0, 10.0124),
            ),
        ):
            snapshot = await cog.collect_snapshot(guild)

        self.assertTrue(snapshot.ready)
        self.assertTrue(snapshot.mongo_available)
        self.assertEqual(snapshot.started_at, started_at)
        self.assertEqual(snapshot.latency_ms, 124)
        self.assertEqual(snapshot.mongo_latency_ms, 12)
        self.assertEqual(snapshot.connected_guilds, 2)
        self.assertEqual(snapshot.cached_members, 6)
        self.assertEqual(snapshot.guild_members, 20)
        self.assertEqual(snapshot.guild_channels, 3)
        self.assertEqual(snapshot.retained_logs, 321)
        self.assertEqual(snapshot.recent_statuses, {"succeeded": 8, "failed": 2})
        cog.db.command.assert_called_once_with("ping")
        cog.logs.count_documents.assert_called_once_with({"guild_id": 41})
        match = cog.logs.aggregate.call_args.args[0][0]["$match"]
        self.assertEqual(match["guild_id"], 41)
        self.assertEqual(match["created_at"], {"$gte": now - timedelta(days=1)})

        cog.collect_snapshot = AsyncMock(return_value=snapshot)
        embed = await cog.build_dashboard_embed(guild)
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.color, discord.Color.green())
        self.assertIn("development", rendered)
        self.assertIn("124 ms", rendered)
        self.assertIn("321", rendered)
        self.assertIn("✅ 8", rendered)
        self.assertIn("❌ 2", rendered)

    async def test_mongo_failure_returns_degraded_snapshot_and_embed(self) -> None:
        cog, guild, _ = self.make_dashboard_cog()
        cog.db.command.side_effect = PyMongoError("Mongo unavailable")

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            snapshot = await cog.collect_snapshot(guild)

        self.assertFalse(snapshot.mongo_available)
        self.assertIsNone(snapshot.mongo_latency_ms)
        self.assertIsNone(snapshot.retained_logs)
        self.assertEqual(snapshot.recent_statuses, {})
        cog.logs.count_documents.assert_not_called()
        cog.logs.aggregate.assert_not_called()
        log_failure.assert_called_once()

        cog.collect_snapshot = AsyncMock(return_value=snapshot)
        embed = await cog.build_dashboard_embed(guild)
        rendered = "\n".join(field.value for field in embed.fields)
        self.assertEqual(embed.color, discord.Color.orange())
        self.assertIn("❌ Ping: **Không khả dụng**", rendered)
        self.assertIn("Log đang giữ: **Không khả dụng**", rendered)

    def test_process_recorder_timestamp_precedes_legacy_uptime_sources(self) -> None:
        cog, _, legacy_started_at = self.make_dashboard_cog()
        process_started_at = datetime(2026, 8, 25, 8, 30, tzinfo=UTC)
        cog.bot.lifecycle_recorder = SimpleNamespace(
            process_started_at=process_started_at
        )

        self.assertEqual(cog._bot_started_at(), process_started_at)
        self.assertNotEqual(process_started_at, legacy_started_at)


class TestOwnerGlobalViews(unittest.IsolatedAsyncioTestCase):
    async def test_owner_view_is_opener_source_admin_and_owner_bound(self) -> None:
        bot = SimpleNamespace(is_owner=AsyncMock(return_value=True))
        view = BotOwnerGuildAdminView(bot=bot, guild_id=41, author_id=77)

        wrong_source = make_interaction(guild_id=99)
        wrong_opener = make_interaction(guild_id=41, user_id=88)
        lost_admin = make_interaction(guild_id=41, administrator=False)
        allowed = make_interaction(guild_id=41)

        self.assertFalse(await view.interaction_check(wrong_source))
        self.assertFalse(await view.interaction_check(wrong_opener))
        self.assertFalse(await view.interaction_check(lost_admin))
        self.assertTrue(await view.interaction_check(allowed))
        bot.is_owner.assert_awaited_once_with(allowed.user)

        bot.is_owner.reset_mock()
        bot.is_owner.return_value = False
        removed_owner = make_interaction(guild_id=41)
        self.assertFalse(await view.interaction_check(removed_owner))
        removed_owner.response.send_message.assert_awaited_once()

    async def test_lifecycle_history_renders_latest_cache_and_degraded_warning(
        self,
    ) -> None:
        occurred_at = datetime(2026, 8, 29, 4, 5)
        recorder = SimpleNamespace(
            environment="development",
            fetch_recent=AsyncMock(
                return_value=(
                    [
                        {
                            "_id": "event-1",
                            "event_type": "initial_ready",
                            "occurred_at": occurred_at,
                            "process_started_at": datetime(2026, 8, 29, 4, 0),
                            "environment": "development",
                            "guild_count": 12,
                        }
                    ],
                    False,
                )
            ),
        )
        bot = SimpleNamespace(
            lifecycle_recorder=recorder,
            environment="production",
            is_owner=AsyncMock(return_value=True),
        )
        view = LifecycleHistoryView(bot=bot, guild_id=41, author_id=77)

        await view.load_events()
        embed = view.build_embed()

        recorder.fetch_recent.assert_awaited_once_with(limit=10)
        self.assertEqual(embed.color, discord.Color.orange())
        self.assertIn("MongoDB không khả dụng", embed.description)
        self.assertIn("Ready đầu tiên", embed.fields[0].name)
        expected_epoch = int(occurred_at.replace(tzinfo=UTC).timestamp())
        self.assertIn(f"<t:{expected_epoch}:F>", embed.fields[0].value)
        self.assertIn("Server quan sát: **12**", embed.fields[0].value)

    async def test_lifecycle_refresh_keeps_panel_private_and_updates_embed(
        self,
    ) -> None:
        recorder = SimpleNamespace(
            environment="production",
            fetch_recent=AsyncMock(
                side_effect=[
                    ([], True),
                    (
                        [
                            {
                                "_id": "resume-1",
                                "event_type": "resumed",
                                "occurred_at": datetime(2026, 8, 29, tzinfo=UTC),
                                "process_started_at": datetime(
                                    2026, 8, 28, tzinfo=UTC
                                ),
                                "guild_count": 3,
                            }
                        ],
                        True,
                    ),
                ]
            ),
        )
        bot = SimpleNamespace(
            lifecycle_recorder=recorder,
            is_owner=AsyncMock(return_value=True),
        )
        view = LifecycleHistoryView(bot=bot, guild_id=41, author_id=77)
        await view.load_events()
        interaction = make_interaction(guild_id=41)

        await view.refresh.callback(interaction)

        self.assertEqual(recorder.fetch_recent.await_count, 2)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        refreshed_embed = interaction.edit_original_response.await_args.kwargs[
            "embed"
        ]
        self.assertIn("Khôi phục phiên", refreshed_embed.fields[0].name)

    async def test_joined_servers_are_sorted_paginated_and_source_protected(
        self,
    ) -> None:
        source = make_guild(41, "alpha", member_count=8, channel_count=3)
        duplicate_high = make_guild(90, "Duplicate")
        duplicate_low = make_guild(80, "duplicate")
        long_name = "x" * 140
        guilds = [
            make_guild(200 + index, f"Server {index:02d}")
            for index in range(9)
        ]
        guilds.extend([duplicate_high, source, duplicate_low, make_guild(500, long_name)])
        cog = make_owner_cog(guilds)
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)

        self.assertEqual(
            [guild.id for guild in view.guilds[:3]],
            [41, 80, 90],
        )
        self.assertEqual(len(view.guild_select.options), JOINED_SERVER_PAGE_SIZE)
        self.assertLessEqual(
            max(len(option.label) for option in view.guild_select.options),
            100,
        )
        self.assertFalse(view.next_page.disabled)
        self.assertEqual(view.guild_select.row, 0)
        self.assertEqual(view.previous_page.row, 1)
        self.assertEqual(view.leave_server.row, 2)

        interaction = make_interaction(guild_id=41)
        await view.select_guild(interaction, "41")

        self.assertTrue(view.leave_server.disabled)
        source_field = next(field for field in view.build_embed().fields if "alpha" in field.name)
        self.assertTrue(source_field.name.startswith("🔒"))
        self.assertIn("Được bảo vệ", source_field.value)

        next_interaction = make_interaction(guild_id=41)
        await view.next_page.callback(next_interaction)
        self.assertEqual(view.page, 1)
        self.assertEqual(len(view.guild_select.options), 3)
        self.assertIsNone(view.selected_guild_id)

    async def test_leave_requires_two_confirmations_and_audits_source(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target", member_count=7, channel_count=2)
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        select_interaction = make_interaction(guild_id=41)
        await view.select_guild(select_interaction, "52")

        first = make_interaction(guild_id=41)
        await view.leave_server.callback(first)

        self.assertTrue(view.leave_armed)
        self.assertEqual(view.leave_server.label, "Xác nhận rời")
        target.leave.assert_not_awaited()
        armed_embed = first.edit_original_response.await_args.kwargs["embed"]
        warning = next(
            field for field in armed_embed.fields if "xác nhận lần hai" in field.name
        )
        self.assertIn("Target", warning.value)
        self.assertIn("`52`", warning.value)
        self.assertIn("link mời mới", warning.value)

        second = make_interaction(guild_id=41)
        await view.leave_server.callback(second)

        target.leave.assert_awaited_once_with()
        cog.record_admin_action.assert_awaited_once()
        audit = cog.record_admin_action.await_args.kwargs
        self.assertIs(audit["interaction"], second)
        self.assertEqual(audit["action"], "leave_guild")
        self.assertEqual(audit["status"], "succeeded")
        self.assertEqual(audit["details"]["target_guild_id"], 52)
        self.assertTrue(view.completed)
        self.assertTrue(all(child.disabled for child in view.children))

    async def test_final_leave_rechecks_live_admin_owner_and_source(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()
        await view.leave_server.callback(make_interaction(guild_id=41))

        lost_admin = make_interaction(guild_id=41, administrator=False)
        await view.leave_server.callback(lost_admin)

        target.leave.assert_not_awaited()
        lost_admin.response.send_message.assert_awaited_once()
        cog.record_admin_action.assert_not_awaited()

        cog.bot.is_owner.return_value = False
        owner_removed = make_interaction(guild_id=41)
        await view.leave_server.callback(owner_removed)
        target.leave.assert_not_awaited()

    async def test_final_leave_rechecks_owner_after_processing_message(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        cog = make_owner_cog([source, target])
        cog.bot.is_owner.side_effect = [True, False]
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()
        await view.leave_server.callback(make_interaction(guild_id=41))
        final = make_interaction(guild_id=41)

        await view.leave_server.callback(final)

        self.assertEqual(cog.bot.is_owner.await_count, 2)
        target.leave.assert_not_awaited()
        final.response.send_message.assert_awaited_once()
        self.assertFalse(view.leave_armed)

    async def test_stale_target_is_not_left_and_failure_is_audited(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        cog = make_owner_cog([source, target])
        cog.bot.get_guild.side_effect = lambda guild_id: source if guild_id == 41 else None
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()
        await view.leave_server.callback(make_interaction(guild_id=41))
        final = make_interaction(guild_id=41)

        await view.leave_server.callback(final)

        target.leave.assert_not_awaited()
        audit = cog.record_admin_action.await_args.kwargs
        self.assertEqual(audit["status"], "failed")
        self.assertEqual(audit["details"]["reason"], "target_guild_missing")
        self.assertIsNone(view.selected_guild_id)
        final.followup.send.assert_awaited_once()

    async def test_http_failure_can_be_confirmed_again_and_retried(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        response = MagicMock(status=503, reason="Unavailable")
        target.leave.side_effect = [
            discord.HTTPException(response, "temporary"),
            None,
        ]
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()

        await view.leave_server.callback(make_interaction(guild_id=41))
        failed = make_interaction(guild_id=41)
        await view.leave_server.callback(failed)

        self.assertFalse(view.leave_armed)
        self.assertFalse(view.leave_server.disabled)
        self.assertEqual(
            cog.record_admin_action.await_args_list[0].kwargs["status"],
            "failed",
        )

        await view.leave_server.callback(make_interaction(guild_id=41))
        await view.leave_server.callback(make_interaction(guild_id=41))

        self.assertEqual(target.leave.await_count, 2)
        self.assertEqual(
            [item.kwargs["status"] for item in cog.record_admin_action.await_args_list],
            ["failed", "succeeded"],
        )

    async def test_unexpected_leave_failure_releases_target_for_retry(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        target.leave.side_effect = [RuntimeError("local failure"), None]
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()

        await view.leave_server.callback(make_interaction(guild_id=41))
        failed = make_interaction(guild_id=41)
        with patch.object(dashboard_module.logger, "exception") as log_failure:
            await view.leave_server.callback(failed)

        self.assertNotIn(52, cog._guild_leave_in_flight)
        self.assertNotIn(52, cog._departed_guild_ids)
        self.assertFalse(view.leave_armed)
        self.assertFalse(view.leave_server.disabled)
        log_failure.assert_called_once()
        self.assertEqual(
            cog.record_admin_action.await_args.kwargs["details"]["error_type"],
            "RuntimeError",
        )

        await view.leave_server.callback(make_interaction(guild_id=41))
        await view.leave_server.callback(make_interaction(guild_id=41))

        self.assertEqual(target.leave.await_count, 2)
        self.assertTrue(view.completed)

    async def test_concurrent_final_click_calls_discord_leave_only_once(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_leave() -> None:
            started.set()
            await release.wait()

        target.leave.side_effect = delayed_leave
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        view.selected_guild_id = 52
        view._sync_controls()
        await view.leave_server.callback(make_interaction(guild_id=41))

        final_task = asyncio.create_task(
            view.leave_server.callback(make_interaction(guild_id=41))
        )
        await started.wait()
        duplicate = make_interaction(guild_id=41)
        await view.leave_server.callback(duplicate)
        release.set()
        await final_task

        target.leave.assert_awaited_once_with()
        duplicate.response.send_message.assert_awaited_once()

    async def test_source_server_can_never_be_selected_for_leave(self) -> None:
        source = make_guild(41, "Source")
        cog = make_owner_cog([source])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        await view.select_guild(make_interaction(guild_id=41), "41")

        self.assertTrue(view.leave_server.disabled)
        crafted = make_interaction(guild_id=41)
        await view.leave_server.callback(crafted)

        source.leave.assert_not_awaited()
        cog.record_admin_action.assert_not_awaited()
        crafted.response.send_message.assert_awaited_once()

    async def test_select_rejects_id_outside_current_page_and_bot_cache(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        cog = make_owner_cog([source, target])
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        interaction = make_interaction(guild_id=41)

        await view.select_guild(interaction, "999999")

        self.assertIsNone(view.selected_guild_id)
        self.assertTrue(view.leave_server.disabled)
        interaction.followup.send.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()

    async def test_server_refresh_reloads_clamps_and_resets_selection(self) -> None:
        source = make_guild(41, "Source")
        guilds = [source] + [
            make_guild(100 + index, f"Server {index:02d}")
            for index in range(JOINED_SERVER_PAGE_SIZE + 1)
        ]
        cog = make_owner_cog(guilds)
        view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        await view.next_page.callback(make_interaction(guild_id=41))
        view.selected_guild_id = view._page_guilds()[0].id
        view.leave_armed = True
        cog.bot.guilds = [source]
        interaction = make_interaction(guild_id=41)

        await view.refresh_servers.callback(interaction)

        self.assertEqual(view.page, 0)
        self.assertEqual([guild.id for guild in view.guilds], [41])
        self.assertIsNone(view.selected_guild_id)
        self.assertFalse(view.leave_armed)
        self.assertTrue(view.next_page.disabled)
        interaction.edit_original_response.assert_awaited_once()

    async def test_two_panels_serialize_leave_for_same_target(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_leave() -> None:
            started.set()
            await release.wait()

        target.leave.side_effect = delayed_leave
        cog = make_owner_cog([source, target])
        first_view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        second_view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        for view in (first_view, second_view):
            view.selected_guild_id = 52
            view._sync_controls()
            await view.leave_server.callback(make_interaction(guild_id=41))

        first_task = asyncio.create_task(
            first_view.leave_server.callback(make_interaction(guild_id=41))
        )
        await started.wait()
        blocked = make_interaction(guild_id=41)
        await second_view.leave_server.callback(blocked)
        release.set()
        await first_task

        target.leave.assert_awaited_once_with()
        statuses = [
            item.kwargs["status"]
            for item in cog.record_admin_action.await_args_list
        ]
        self.assertEqual(statuses, ["failed", "succeeded"])
        self.assertIn(52, cog._departed_guild_ids)
        blocked.followup.send.assert_awaited_once()

    async def test_cancelled_discord_leave_stays_latched_across_panels(self) -> None:
        source = make_guild(41, "Source")
        target = make_guild(52, "Target")
        started = asyncio.Event()
        never_release = asyncio.Event()

        async def cancelled_leave() -> None:
            started.set()
            await never_release.wait()

        target.leave.side_effect = cancelled_leave
        cog = make_owner_cog([source, target])
        first_view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        second_view = JoinedServerView(cog=cog, guild_id=41, author_id=77)
        for view in (first_view, second_view):
            view.selected_guild_id = 52
            view._sync_controls()
            await view.leave_server.callback(make_interaction(guild_id=41))

        task = asyncio.create_task(
            first_view.leave_server.callback(make_interaction(guild_id=41))
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        blocked = make_interaction(guild_id=41)
        await second_view.leave_server.callback(blocked)

        target.leave.assert_awaited_once_with()
        self.assertIn(52, cog._departed_guild_ids)
        blocked.followup.send.assert_awaited_once()


class TestOperationViews(unittest.IsolatedAsyncioTestCase):
    async def test_view_rechecks_same_guild_and_live_administrator(self) -> None:
        view = GuildAdminView(guild_id=41, author_id=77)

        wrong_guild = make_interaction(guild_id=99)
        stranger = make_interaction(guild_id=41, user_id=88)
        lost_permission = make_interaction(guild_id=41, administrator=False)
        allowed = make_interaction(guild_id=41)

        self.assertFalse(await view.interaction_check(wrong_guild))
        self.assertFalse(await view.interaction_check(stranger))
        self.assertFalse(await view.interaction_check(lost_permission))
        self.assertTrue(await view.interaction_check(allowed))
        for denied in (wrong_guild, stranger, lost_permission):
            denied.response.send_message.assert_awaited_once()
            self.assertTrue(
                denied.response.send_message.await_args.kwargs["ephemeral"]
            )
        allowed.response.send_message.assert_not_awaited()

    async def test_dashboard_timeout_disables_every_control_and_edits_message(
        self,
    ) -> None:
        view = OperationDashboardView(cog=SimpleNamespace(), guild_id=41)
        message = SimpleNamespace(edit=AsyncMock())
        view.message = message
        self.assertTrue(all(not child.disabled for child in view.children))

        await view.on_timeout()

        self.assertTrue(all(child.disabled for child in view.children))
        message.edit.assert_awaited_once_with(view=view)

    async def test_clear_all_requires_two_confirmations_then_audits_after_delete(
        self,
    ) -> None:
        calls = MagicMock()
        prune_logs = AsyncMock(return_value=12)
        record_admin_action = AsyncMock()
        calls.attach_mock(prune_logs, "prune_logs")
        calls.attach_mock(record_admin_action, "record_admin_action")
        cog = SimpleNamespace(
            count_prunable_logs=AsyncMock(return_value=12),
            prune_logs=prune_logs,
            record_admin_action=record_admin_action,
        )
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        view.range_key = "all"
        await view.refresh_preview()
        interaction = make_interaction(guild_id=41)

        await view.confirm.callback(interaction)

        self.assertTrue(view.clear_all_armed)
        self.assertEqual(view.confirm.label, "Xác nhận xóa toàn bộ")
        prune_logs.assert_not_awaited()
        interaction.response.defer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()

        await view.confirm.callback(interaction)

        prune_logs.assert_awaited_once_with(guild_id=41, cutoff=None)
        record_admin_action.assert_awaited_once()
        action_kwargs = record_admin_action.await_args.kwargs
        self.assertEqual(action_kwargs["action"], "prune_logs")
        self.assertEqual(action_kwargs["status"], "succeeded")
        self.assertEqual(action_kwargs["details"]["deleted_count"], 12)
        self.assertEqual(
            [
                entry
                for entry in calls.mock_calls
                if entry[0] in {"prune_logs", "record_admin_action"}
            ],
            [
                call.prune_logs(guild_id=41, cutoff=None),
                call.record_admin_action(
                    interaction=interaction,
                    action="prune_logs",
                    status="succeeded",
                    details={
                        "range": "all",
                        "cutoff": None,
                        "deleted_count": 12,
                    },
                ),
            ],
        )
        self.assertTrue(view.completed)

    async def test_export_download_splits_files_and_records_admin_action(
        self,
    ) -> None:
        documents = [
            {
                "created_at": datetime(2026, 8, 26, tzinfo=UTC),
                "event_type": "command",
                "status": "succeeded",
                "command_name": "big_speaker",
                "arguments": "x" * 600,
                "guild_id": 41,
            },
            {
                "created_at": datetime(2026, 8, 25, tzinfo=UTC),
                "event_type": "command",
                "status": "succeeded",
                "command_name": "quote",
                "arguments": "y" * 600,
                "guild_id": 41,
            },
        ]
        cog = SimpleNamespace(
            fetch_export_documents=AsyncMock(return_value=documents),
            record_admin_action=AsyncMock(return_value=True),
        )
        view = ExportLogView(cog=cog, guild_id=41, author_id=77)
        interaction = make_interaction(guild_id=41)
        interaction.guild.filesize_limit = dashboard_module.UPLOAD_SIZE_RESERVE + 1_024

        await view.download.callback(interaction)

        self.assertEqual(view.range_key, "all")
        self.assertTrue(view.completed)
        self.assertEqual(interaction.followup.send.await_count, 2)
        filenames = [
            item.kwargs["file"].filename
            for item in interaction.followup.send.await_args_list
        ]
        self.assertTrue(all(name.endswith(".csv") for name in filenames))
        self.assertIn("part-01-of-02", filenames[0])
        self.assertIn("part-02-of-02", filenames[1])
        cog.record_admin_action.assert_awaited_once()
        action = cog.record_admin_action.await_args.kwargs
        self.assertEqual(action["action"], "export_logs")
        self.assertEqual(action["status"], "succeeded")
        self.assertEqual(action["details"], {"range": "all", "rows": 2, "parts": 2})

    async def test_prune_cancel_never_deletes_records(self) -> None:
        cog = SimpleNamespace(prune_logs=AsyncMock())
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        interaction = make_interaction(guild_id=41)

        await view.cancel.callback(interaction)

        cog.prune_logs.assert_not_awaited()
        self.assertTrue(view.is_finished())
        interaction.edit_original_response.assert_awaited_once()

    def test_audit_embed_treats_naive_mongo_dates_as_utc(self) -> None:
        view = AuditLogView(
            cog=SimpleNamespace(),
            guild_id=41,
            author_id=77,
        )
        created_at = datetime(2026, 8, 26, 12, 0)
        view.documents = [
            {
                "created_at": created_at,
                "event_type": "command",
                "status": "succeeded",
                "command_name": "ping",
                "actor_id": 77,
                "actor_name": "admin",
                "channel_id": 501,
            }
        ]
        view.total = 1

        embed = view.build_embed()

        expected_epoch = int(created_at.replace(tzinfo=UTC).timestamp())
        self.assertIn(f"<t:{expected_epoch}:R>", embed.fields[0].value)

    async def test_failed_prune_range_preview_preserves_confirmed_cutoff(
        self,
    ) -> None:
        count_prunable_logs = AsyncMock(
            side_effect=[8, PyMongoError("Mongo unavailable")]
        )
        cog = SimpleNamespace(count_prunable_logs=count_prunable_logs)
        view = PruneLogView(cog=cog, guild_id=41, author_id=77)
        view.range_key = "180d"
        view.range_select.set_selected("180d")
        await view.refresh_preview()
        original_cutoff = view.cutoff
        interaction = make_interaction(guild_id=41)

        with patch.object(dashboard_module.logger, "exception"):
            await view._change_range(interaction, "30d")

        self.assertEqual(view.range_key, "180d")
        self.assertEqual(view.cutoff, original_cutoff)
        self.assertEqual(view.matching_count, 8)
        self.assertFalse(view.confirm.disabled)
        interaction.followup.send.assert_awaited_once()

    async def test_bot_status_command_opens_dashboard_without_replacing_server_stats(
        self,
    ) -> None:
        cog = object.__new__(OperationDashboardCog)
        cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
        cog.build_dashboard_embed = AsyncMock(
            return_value=discord.Embed(title="Bot status")
        )
        sent_message = SimpleNamespace(id=123)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=41),
            author=SimpleNamespace(id=77),
            reply=AsyncMock(return_value=sent_message),
        )

        await OperationDashboardCog.show_bot_status.callback(cog, ctx)

        ctx.reply.assert_awaited_once()
        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["view"], OperationDashboardView)
        self.assertEqual(kwargs["view"].timeout, DASHBOARD_TIMEOUT_SECONDS)
        self.assertIs(kwargs["view"].message, sent_message)
        self.assertEqual(
            [child.label for child in kwargs["view"].children],
            ["Làm mới", "Audit logs", "Tải CSV", "Dọn log", "Doctor"],
        )
        self.assertEqual(OperationDashboardCog.show_bot_status.name, "bot_status")
        self.assertEqual(ServerStatsCog.server_stats.name, "server_stats")

    async def test_owner_opened_dashboard_adds_only_the_two_global_controls(
        self,
    ) -> None:
        cog = object.__new__(OperationDashboardCog)
        cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=True))
        cog.build_dashboard_embed = AsyncMock(
            return_value=discord.Embed(title="Bot status")
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=41),
            author=SimpleNamespace(id=77),
            reply=AsyncMock(return_value=SimpleNamespace(id=123)),
        )

        await OperationDashboardCog.show_bot_status.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.owner_id, 77)
        self.assertEqual(
            [child.label for child in view.children],
            [
                "Làm mới",
                "Audit logs",
                "Tải CSV",
                "Dọn log",
                "Doctor",
                "Server đã tham gia",
                "Lịch sử kết nối",
            ],
        )
        self.assertEqual([child.row for child in view.children[:5]], [0] * 5)
        self.assertEqual([child.row for child in view.children[5:]], [1, 1])

    async def test_owner_lookup_failure_still_opens_admin_dashboard(self) -> None:
        cog = object.__new__(OperationDashboardCog)
        cog.bot = SimpleNamespace(
            is_owner=AsyncMock(side_effect=RuntimeError("application unavailable"))
        )
        cog.build_dashboard_embed = AsyncMock(
            return_value=discord.Embed(title="Bot status")
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=41),
            author=SimpleNamespace(id=77),
            reply=AsyncMock(return_value=SimpleNamespace(id=123)),
        )

        with patch.object(dashboard_module.logger, "exception") as log_failure:
            await OperationDashboardCog.show_bot_status.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertIsNone(view.owner_id)
        self.assertEqual(
            [child.label for child in view.children],
            ["Làm mới", "Audit logs", "Tải CSV", "Dọn log", "Doctor"],
        )
        log_failure.assert_called_once()

    async def test_owner_dashboard_button_rechecks_owner_before_opening(self) -> None:
        bot = SimpleNamespace(is_owner=AsyncMock(return_value=False))
        cog = SimpleNamespace(bot=bot)
        view = OperationDashboardView(cog=cog, guild_id=41, owner_id=77)
        interaction = make_interaction(guild_id=41)

        await view.joined_servers.callback(interaction)

        bot.is_owner.assert_awaited_once_with(interaction.user)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.defer.assert_not_awaited()


class TestDoctorView(unittest.IsolatedAsyncioTestCase):
    def make_view(self) -> DoctorView:
        member = SimpleNamespace(
            id=77, guild_permissions=SimpleNamespace(administrator=True)
        )
        guild = SimpleNamespace(id=41, get_member=MagicMock(return_value=member))
        channel = SimpleNamespace(id=501, guild=guild)
        bot = SimpleNamespace(get_guild=MagicMock(return_value=guild))
        return DoctorView(
            cog=SimpleNamespace(bot=bot),
            guild_id=41,
            author_id=77,
            channel=channel,
        )

    async def test_doctor_button_opens_private_panel_for_clicking_admin(self) -> None:
        interaction = make_interaction(user_id=88)
        interaction.guild.get_member = MagicMock(return_value=interaction.user)
        interaction.channel = SimpleNamespace(id=501, guild=interaction.guild)
        bot = SimpleNamespace(get_guild=MagicMock(return_value=interaction.guild))
        dashboard = OperationDashboardView(cog=SimpleNamespace(bot=bot), guild_id=41)
        button = dashboard.children[4]
        self.assertEqual(button.label, "Doctor")
        self.assertEqual(str(button.emoji), "🩺")
        self.assertEqual(button.row, 0)
        sent_message = SimpleNamespace(edit=AsyncMock())
        interaction.followup.send.return_value = sent_message

        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=[SetupCheck("warning", "Cấu hình", "Chưa có kênh", "Đặt kênh.")],
        ) as collect:
            await button.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertTrue(kwargs["wait"])
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
        panel = kwargs["view"]
        self.assertIsInstance(panel, DoctorView)
        self.assertEqual(panel.author_id, 88)
        self.assertEqual(panel.guild_id, 41)
        self.assertIs(panel.message, sent_message)
        self.assertEqual(panel.timeout, 180)
        collect.assert_awaited_once_with(bot, interaction.guild, interaction.channel)

    async def test_panel_rechecks_guild_opener_and_current_admin_permission(self) -> None:
        view = self.make_view()
        denied = (
            make_interaction(guild_id=None),
            make_interaction(guild_id=99),
            make_interaction(user_id=88),
            make_interaction(administrator=False),
        )
        for interaction in denied:
            self.assertFalse(await view.interaction_check(interaction))
            kwargs = interaction.response.send_message.await_args.kwargs
            self.assertTrue(kwargs["ephemeral"])
            self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertTrue(await view.interaction_check(make_interaction()))

    async def test_load_sorts_errors_first_filters_success_and_records_scan_time(self) -> None:
        view = self.make_view()
        checks = [
            SetupCheck("warning", "Zulu", "Cảnh báo cuối", "Sửa Z."),
            SetupCheck("ok", "Healthy", "Đã đúng"),
            SetupCheck("error", "Beta", "Lỗi B", "Sửa B."),
            SetupCheck("warning", "Alpha", "Cảnh báo đầu", "Sửa A."),
            SetupCheck("error", "Alpha", "Lỗi A", "Sửa A."),
        ]
        scanned_at = datetime(2026, 9, 8, 10, 20, tzinfo=UTC)
        view.page = 9
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=checks,
        ), patch.object(dashboard_module.discord.utils, "utcnow", return_value=scanned_at):
            await view.load_checks()

        self.assertEqual(view.findings, [checks[4], checks[2], checks[3], checks[0]])
        self.assertEqual(view.page, 0)
        self.assertEqual(view.generated_at, scanned_at)
        embed = view.build_embed()
        self.assertEqual(embed.timestamp, scanned_at)
        self.assertIn("2", embed.description)
        self.assertIn("lỗi", embed.description.lower())
        self.assertIn("cảnh báo", embed.description.lower())
        self.assertTrue(any("Sửa A." in field.value for field in embed.fields))

    async def test_missing_guild_cache_has_error_without_running_collector(self) -> None:
        view = self.make_view()
        view.cog.bot.get_guild.return_value = None
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
        ) as collect:
            await view.load_checks()

        collect.assert_not_awaited()
        self.assertEqual(len(view.findings), 1)
        self.assertEqual(view.findings[0].level, "error")
        self.assertIn("server", view.findings[0].detail.lower())
        self.assertTrue(view.findings[0].fix)

    async def test_pages_show_five_findings_and_navigation_stays_in_bounds(self) -> None:
        view = self.make_view()
        checks = [
            SetupCheck("warning", f"Feature {index:02}", f"Problem {index}", "Fix.")
            for index in range(11)
        ]
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=checks,
        ) as collect:
            await view.load_checks()
            self.assertEqual(len(view.build_embed().fields), 5)
            self.assertTrue(view.previous_page.disabled)
            self.assertFalse(view.next_page.disabled)
            await view.next_page.callback(make_interaction())
            self.assertEqual(view.page, 1)
            self.assertEqual(len(view.build_embed().fields), 5)
            self.assertIn("Feature 05", view.build_embed().fields[0].name)
            await view.next_page.callback(make_interaction())
            self.assertEqual(view.page, 2)
            self.assertEqual(len(view.build_embed().fields), 1)
            self.assertTrue(view.next_page.disabled)
            await view.next_page.callback(make_interaction())
            self.assertEqual(view.page, 2)
            await view.previous_page.callback(make_interaction())
            await view.previous_page.callback(make_interaction())
            await view.previous_page.callback(make_interaction())
            self.assertEqual(view.page, 0)
            self.assertTrue(view.previous_page.disabled)
            collect.assert_awaited_once()

    async def test_refresh_reruns_scan_resets_page_and_suppresses_mentions(self) -> None:
        view = self.make_view()
        initial = [SetupCheck("error", str(index), "Old", "Fix") for index in range(9)]
        replacement = [SetupCheck("warning", "@everyone", "<@123> problem", "Fix")]
        interaction = make_interaction()
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            side_effect=[initial, replacement],
        ) as collect:
            await view.load_checks()
            await view.next_page.callback(make_interaction())
            await view.refresh.callback(interaction)

        self.assertEqual(collect.await_count, 2)
        self.assertEqual(view.findings, replacement)
        self.assertEqual(view.page, 0)
        self.assertTrue(view.previous_page.disabled)
        self.assertTrue(view.next_page.disabled)
        self.assertTrue(interaction.response.defer.await_args.kwargs["ephemeral"])
        kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertIs(kwargs["view"], view)
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_healthy_scan_is_explicit_and_disables_navigation(self) -> None:
        view = self.make_view()
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=[SetupCheck("ok", "Database", "OK")],
        ):
            await view.load_checks()

        self.assertEqual(view.findings, [])
        self.assertEqual(view.page, 0)
        self.assertTrue(view.previous_page.disabled)
        self.assertTrue(view.next_page.disabled)
        embed = view.build_embed()
        displayed = " ".join(
            [embed.description or ""]
            + [f"{field.name} {field.value}" for field in embed.fields]
        ).lower()
        self.assertIn("không", displayed)
        self.assertIn("cảnh báo", displayed)

    async def test_long_findings_fit_discord_embed_limits(self) -> None:
        view = self.make_view()
        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=[
                SetupCheck("error", "Name " * 1_000, "Detail " * 2_000, "Fix " * 2_000)
                for _ in range(6)
            ],
        ):
            await view.load_checks()

        embed = view.build_embed()
        self.assertEqual(len(embed.fields), 5)
        self.assertLessEqual(len(embed), 6_000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description), 4_096)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1_024)
            self.assertIn("Fix", field.value)

    async def test_timeout_disables_all_controls_and_edits_private_message(self) -> None:
        view = self.make_view()
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        self.assertTrue(all(child.disabled for child in view.children))
        view.message.edit.assert_awaited_once_with(view=view)

    async def test_refresh_serializes_scans_and_navigation(self) -> None:
        view = self.make_view()
        initial = [SetupCheck("error", str(index), "Old", "Fix") for index in range(9)]
        entered = asyncio.Event()
        release = asyncio.Event()
        active_scans = 0
        maximum_scans = 0

        async def slow_scan(*args):
            nonlocal active_scans, maximum_scans
            active_scans += 1
            maximum_scans = max(maximum_scans, active_scans)
            entered.set()
            try:
                await release.wait()
                return [SetupCheck("warning", "Current", "New", "Fix")]
            finally:
                active_scans -= 1

        with patch.object(
            dashboard_module, "collect_doctor_checks", new_callable=AsyncMock,
            return_value=initial,
        ):
            await view.load_checks()
        await view.next_page.callback(make_interaction())
        self.assertEqual(view.page, 1)

        with patch.object(dashboard_module, "collect_doctor_checks", side_effect=slow_scan):
            first = asyncio.create_task(view.refresh.callback(make_interaction()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            second = asyncio.create_task(view.refresh.callback(make_interaction()))
            navigate = asyncio.create_task(view.previous_page.callback(make_interaction()))
            try:
                await asyncio.sleep(0)
                self.assertEqual(view.page, 1)
                self.assertEqual(maximum_scans, 1)
            finally:
                release.set()
                await asyncio.wait_for(asyncio.gather(first, second, navigate), timeout=1)

        self.assertEqual(maximum_scans, 1)
        self.assertEqual(view.page, 0)
        self.assertEqual(len(view.findings), 1)
        self.assertTrue(view.previous_page.disabled)
        self.assertTrue(view.next_page.disabled)

    async def test_revoked_permission_during_scan_prevents_report_update(self) -> None:
        view = self.make_view()
        interaction = make_interaction()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_scan(*args):
            entered.set()
            await release.wait()
            return [SetupCheck("error", "Environment", "Missing setting", "Set it")]

        with patch.object(dashboard_module, "collect_doctor_checks", side_effect=slow_scan):
            refreshing = asyncio.create_task(view.refresh.callback(interaction))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                cached_member = view.cog.bot.get_guild(41).get_member(77)
                cached_member.guild_permissions.administrator = False
                self.assertTrue(interaction.user.guild_permissions.administrator)
                interaction.response.is_done.return_value = True
            finally:
                release.set()
                await asyncio.wait_for(refreshing, timeout=1)

        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_unavailable_current_member_fails_private_access_closed(self) -> None:
        view = self.make_view()
        view.cog.bot.get_guild(41).get_member.return_value = None
        interaction = make_interaction()

        self.assertFalse(await view.interaction_check(interaction))

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])


class TestServerStatsRegression(unittest.IsolatedAsyncioTestCase):
    async def test_existing_server_stats_counts_and_rendering_are_unchanged(self) -> None:
        cog = ServerStatsCog(SimpleNamespace())
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        cog.start_time = now - timedelta(days=1, hours=2, minutes=3)
        ctx = SimpleNamespace(send=AsyncMock())

        await cog.on_command(ctx)
        await cog.on_command(ctx)
        await cog.on_command_error(ctx, BetaFunctionError("expected denial"))
        await cog.on_command_error(ctx, ShutdownInProgress())
        await cog.on_command_error(ctx, commands.CommandError("unexpected"))
        with patch("cogs.operation.server_stats.discord.utils.utcnow", return_value=now):
            await ServerStatsCog.server_stats.callback(cog, ctx)

        self.assertEqual(cog.command_count, 2)
        self.assertEqual(cog.exception_count, 1)
        ctx.send.assert_awaited_once_with(
            "**Thống kê máy chủ:**\n"
            "- Khởi chạy lúc: 2026-08-25 09:57:00 UTC\n"
            "- Thời gian hoạt động: 1 ngày, 2 giờ, 3 phút\n"
            "- Lệnh đã thực thi: 2\n"
            "- Lệnh lỗi: 1"
        )

    async def test_existing_server_stats_permission_and_cooldown_are_unchanged(
        self,
    ) -> None:
        command = ServerStatsCog.server_stats
        self.assertEqual(command.name, "server_stats")
        self.assertEqual(len(command.checks), 1)
        self.assertTrue(
            command.checks[0](
                SimpleNamespace(
                    permissions=discord.Permissions(administrator=True)
                )
            )
        )
        with self.assertRaises(commands.MissingPermissions):
            command.checks[0](
                SimpleNamespace(
                    permissions=discord.Permissions(administrator=False)
                )
            )

        buckets = command._buckets
        self.assertEqual(buckets._cooldown.rate, 1)
        self.assertEqual(buckets._cooldown.per, 10)
        self.assertIs(buckets._type, commands.BucketType.guild)


if __name__ == "__main__":
    unittest.main()
