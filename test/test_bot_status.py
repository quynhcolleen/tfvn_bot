import asyncio
import json
import tempfile
import time
import unittest
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

from cogs.operation import bot_status as status_module
from cogs.operation.bot_status import (
    BotStatusCog,
    STATUS_FILE,
    STREAM_URL,
    load_statuses,
    make_activity,
    make_override_status,
    parse_duration,
)


class TestBotStatusData(unittest.TestCase):
    def test_shipped_status_data_separates_custom_and_action_schema(self) -> None:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))

        for entry in payload["bot_statuses"]:
            if entry["type"] == "CUSTOM":
                self.assertIsInstance(entry.get("think"), str)
                self.assertTrue(entry["think"].strip())
                self.assertNotIn("text", entry)
            else:
                self.assertIsInstance(entry.get("text"), str)
                self.assertTrue(entry["text"].strip())
                self.assertNotIn("think", entry)

        self.assertEqual(len(load_statuses()), len(payload["bot_statuses"]))

    def test_load_statuses_uses_field_for_activity_type(self) -> None:
        payload = {
            "bot_statuses": [
                {
                    "type": "playing",
                    "text": "  trốn tìm  ",
                },
                {"type": "custom", "think": "  Đang suy nghĩ...  "},
                {"type": "watching", "text": "  một bộ phim  "},
                {"type": "invalid", "text": "skip me"},
            ]
        }
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "statuses.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            statuses = load_statuses(path)

        self.assertEqual(
            statuses,
            [
                {
                    "type": "PLAYING",
                    "text": "trốn tìm",
                },
                {"type": "CUSTOM", "think": "Đang suy nghĩ..."},
                {"type": "WATCHING", "text": "một bộ phim"},
            ],
        )

    def test_make_activity_uses_only_the_selected_display(self) -> None:
        action = make_activity({"type": "PLAYING", "text": "trốn tìm"})
        thought = make_activity({"type": "CUSTOM", "think": "Đang suy nghĩ..."})

        self.assertEqual(action.type, discord.ActivityType.playing)
        self.assertEqual(action.name, "trốn tìm")
        self.assertIsInstance(thought, discord.CustomActivity)
        self.assertEqual(thought.name, "Đang suy nghĩ...")


class TestBotStatusInput(unittest.TestCase):
    def test_duration_accepts_minutes_hours_days_and_boundary_values(self) -> None:
        for value, expected in (
            ("1m", 60),
            ("15m", 900),
            ("1h", 3600),
            ("24h", 86400),
            ("1440m", 86400),
            ("1d", 86400),
            ("2H", 7200),
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_duration(value), expected)

    def test_duration_rejects_out_of_range_and_non_single_unit_inputs(self) -> None:
        for value in (
            "", "0m", "-1m", "+1h", "1.5h", "30s", "1h30m", "1 h", "60",
            "25h", "2d", "1441m", "1m\n2m", "9" * 5000 + "h",
        ):
            with self.subTest(value=value[:30]):
                with self.assertRaises(ValueError):
                    parse_duration(value)

    def test_every_supported_type_reuses_the_existing_activity_builder(self) -> None:
        for name, activity_type in (
            ("PLAYING", discord.ActivityType.playing),
            ("WATCHING", discord.ActivityType.watching),
            ("LISTENING", discord.ActivityType.listening),
            ("STREAMING", discord.ActivityType.streaming),
            ("COMPETING", discord.ActivityType.competing),
            ("CUSTOM", discord.ActivityType.custom),
        ):
            with self.subTest(activity_type=name):
                status = make_override_status("  " + name.lower() + "  ", "  Fortnite  ")
                field = "think" if name == "CUSTOM" else "text"
                self.assertEqual(status, {"type": name, field: "Fortnite"})
                activity = make_activity(status)
                self.assertEqual(activity.type, activity_type)
                self.assertEqual(activity.name, "Fortnite")
                if name == "STREAMING":
                    self.assertEqual(activity.url, STREAM_URL)

    def test_status_text_length_is_checked_after_trimming(self) -> None:
        self.assertEqual(make_override_status("PLAYING", " x ")["text"], "x")
        self.assertEqual(
            make_override_status("PLAYING", "  " + "x" * 128 + "  ")["text"],
            "x" * 128,
        )

    def test_status_rejects_unknown_type_blank_long_and_multiline_text(self) -> None:
        with self.assertRaises(ValueError):
            make_override_status("SLEEPING", "Fortnite")
        for text in ("", "   ", "x" * 129, "first\nsecond", "first\rsecond"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    make_override_status("PLAYING", text)


class TestTemporaryBotStatus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = 1000.0
        self.ready = asyncio.Event()
        self.bot = SimpleNamespace(
            wait_until_ready=self.ready.wait,
            is_ready=lambda: True,
            is_closed=lambda: False,
            change_presence=AsyncMock(),
        )
        self.random_statuses = [
            {"type": "PLAYING", "text": "first random"},
            {"type": "WATCHING", "text": "second random"},
        ]
        for patcher in (
            patch.object(status_module, "time", SimpleNamespace(monotonic=lambda: self.now)),
            patch.object(status_module, "load_statuses", return_value=self.random_statuses),
            patch.object(status_module.random, "choice", side_effect=lambda entries: entries[0]),
            patch.object(status_module.random, "uniform", return_value=600.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cog = BotStatusCog(self.bot)
        self.addAsyncCleanup(self.cog.cog_unload)
        self.fortnite = make_override_status("PLAYING", "Fortnite")

    def make_context(
        self, *, administrator: bool = True, guild_id: int | None = 1,
    ) -> SimpleNamespace:
        permissions = discord.Permissions(administrator=administrator)
        return SimpleNamespace(
            guild=None if guild_id is None else SimpleNamespace(id=guild_id),
            author=SimpleNamespace(id=42, guild_permissions=permissions),
            permissions=permissions,
            prefix="!tf ",
            clean_prefix="!tf ",
            send=AsyncMock(),
        )

    def assert_mentions_disabled(self, ctx: SimpleNamespace) -> None:
        self.assertTrue(ctx.send.await_count)
        for sent in ctx.send.await_args_list:
            self.assertEqual(
                sent.kwargs["allowed_mentions"].to_dict(),
                discord.AllowedMentions.none().to_dict(),
            )

    async def test_set_applies_immediately_and_records_monotonic_and_utc_expiry(self) -> None:
        override = await self.cog.set_override(self.fortnite, 3600)

        self.assertIs(self.cog.override, override)
        self.assertEqual(override.status, self.fortnite)
        self.assertEqual(override.deadline, self.now + 3600)
        self.assertEqual(override.expires_at.tzinfo, timezone.utc)
        self.assertAlmostEqual(
            (override.expires_at - discord.utils.utcnow()).total_seconds(),
            3600,
            delta=2,
        )
        self.assertEqual(self.cog.current_status, self.fortnite)
        self.assertEqual(self.bot.change_presence.await_args.kwargs["activity"].name, "Fortnite")
        self.assertTrue(self.cog._wake_event.is_set())

    async def test_override_pauses_rotation_until_its_exact_deadline(self) -> None:
        await self.cog.update_status()
        previous_random = self.cog.last_status
        await self.cog.set_override(self.fortnite, 60)
        self.bot.change_presence.reset_mock()

        self.now += 59
        self.assertEqual(await self.cog.update_status(), 1)
        self.bot.change_presence.assert_not_awaited()
        self.assertEqual(self.cog.last_status, previous_random)

        self.now += 1
        self.assertEqual(await self.cog.update_status(), 600)
        self.assertIsNone(self.cog.override)
        self.assertNotEqual(self.cog.current_status, previous_random)
        self.assertEqual(self.cog.current_status, self.cog.last_status)
        self.bot.change_presence.assert_awaited_once()

    async def test_old_deadline_cannot_clear_a_replacement_override(self) -> None:
        first = await self.cog.set_override(self.fortnite, 60)
        self.now += 20
        replacement = make_override_status("WATCHING", "a movie")
        second = await self.cog.set_override(replacement, 120)
        self.bot.change_presence.reset_mock()

        self.now = first.deadline
        self.assertEqual(await self.cog.update_status(), 80)
        self.assertIs(self.cog.override, second)
        self.assertEqual(self.cog.current_status, replacement)
        self.bot.change_presence.assert_not_awaited()

    async def test_reset_resumes_rotation_immediately_without_repeating_last_random(self) -> None:
        await self.cog.update_status()
        previous_random = self.cog.last_status
        await self.cog.set_override(self.fortnite, 3600)
        self.now += 11

        self.assertTrue(await self.cog.reset_override())

        self.assertIsNone(self.cog.override)
        self.assertNotEqual(self.cog.current_status, previous_random)
        self.assertEqual(self.cog.current_status, self.cog.last_status)
        self.assertEqual(self.cog._next_rotation_at, self.now + 600)
        self.assertTrue(self.cog._wake_event.is_set())

    async def test_set_failure_preserves_override_and_current_presence_state(self) -> None:
        previous = await self.cog.set_override(self.fortnite, 60)
        self.bot.change_presence.side_effect = RuntimeError("connection unavailable")
        self.now += 11

        with self.assertRaises(RuntimeError):
            await self.cog.set_override(make_override_status("CUSTOM", "replacement"), 120)

        self.assertIs(self.cog.override, previous)
        self.assertEqual(self.cog.current_status, self.fortnite)

    async def test_reset_failure_preserves_override_and_last_random(self) -> None:
        await self.cog.update_status()
        previous_random = self.cog.last_status
        previous = await self.cog.set_override(self.fortnite, 60)
        self.bot.change_presence.side_effect = OSError("gateway unavailable")
        self.now += 11

        with self.assertRaises(OSError):
            await self.cog.reset_override()

        self.assertIs(self.cog.override, previous)
        self.assertEqual(self.cog.current_status, self.fortnite)
        self.assertEqual(self.cog.last_status, previous_random)

    async def test_automatic_failure_retries_after_30_seconds(self) -> None:
        self.bot.change_presence.side_effect = [RuntimeError("disconnected"), None]

        with self.assertLogs(status_module.logger, level="ERROR"):
            self.assertEqual(await self.cog.update_status(), 30)
        self.assertIsNone(self.cog.current_status)
        self.now += 29
        self.assertEqual(await self.cog.update_status(), 1)
        self.assertEqual(self.bot.change_presence.await_count, 1)

        self.now += 1
        self.assertEqual(await self.cog.update_status(), 600)
        self.assertEqual(self.bot.change_presence.await_count, 2)
        self.assertIn(self.cog.current_status, self.random_statuses)

    async def test_expiry_failure_retries_without_extending_the_override(self) -> None:
        override = await self.cog.set_override(self.fortnite, 60)
        self.now = override.deadline
        self.bot.change_presence.side_effect = [RuntimeError("disconnected"), None]

        with self.assertLogs(status_module.logger, level="ERROR"):
            self.assertEqual(await self.cog.update_status(), 30)
        self.assertEqual(self.cog.current_status, self.fortnite)
        self.now += 30
        await self.cog.update_status()

        self.assertIsNone(self.cog.override)
        self.assertIn(self.cog.current_status, self.random_statuses)

    async def test_ready_and_resumed_reapply_unexpired_override(self) -> None:
        override = await self.cog.set_override(self.fortnite, 3600)
        for listener in (self.cog.on_ready, self.cog.on_resumed):
            with self.subTest(listener=listener.__name__):
                self.bot.change_presence.reset_mock()
                self.cog._wake_event.clear()
                await listener()
                self.assertTrue(self.cog._wake_event.is_set())
                await self.cog.update_status()
                self.bot.change_presence.assert_awaited_once()
                self.assertEqual(
                    self.bot.change_presence.await_args.kwargs["activity"].name,
                    "Fortnite",
                )
                self.assertIs(self.cog.override, override)

    async def test_reconnect_after_expiry_resumes_random_instead_of_old_override(self) -> None:
        override = await self.cog.set_override(self.fortnite, 60)
        self.now = override.deadline + 10
        self.bot.change_presence.reset_mock()

        await self.cog.on_resumed()
        await self.cog.update_status()

        self.assertIsNone(self.cog.override)
        self.assertIn(self.cog.current_status, self.random_statuses)
        self.assertNotEqual(
            self.bot.change_presence.await_args.kwargs["activity"].name,
            "Fortnite",
        )

    async def test_reconnect_retries_immediately_despite_pending_backoff(self) -> None:
        self.bot.change_presence.side_effect = [RuntimeError("disconnected"), None]
        with self.assertLogs(status_module.logger, level="ERROR"):
            await self.cog.update_status()

        await self.cog.on_resumed()
        await self.cog.update_status()

        self.assertEqual(self.bot.change_presence.await_count, 2)
        self.assertIn(self.cog.current_status, self.random_statuses)

    async def test_rotation_and_manual_update_are_serialized(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        writes: list[str] = []

        async def slow_presence(*, activity: discord.BaseActivity) -> None:
            writes.append(activity.name)
            if len(writes) == 1:
                entered.set()
                await release.wait()

        self.bot.change_presence.side_effect = slow_presence
        rotation = asyncio.create_task(self.cog.update_status())
        await asyncio.wait_for(entered.wait(), timeout=1)
        manual = asyncio.create_task(self.cog.set_override(self.fortnite, 60))
        try:
            await asyncio.sleep(0)
            self.assertEqual(len(writes), 1)
            self.assertFalse(manual.done())
        finally:
            release.set()
            await asyncio.gather(rotation, manual)

        self.assertEqual(writes, ["first random", "Fortnite"])
        self.assertEqual(self.cog.current_status, self.fortnite)
        self.assertEqual(self.cog.override.status, self.fortnite)

    async def test_rotation_wait_is_interrupted_by_expiry_wakeup(self) -> None:
        first_write = asyncio.Event()

        async def presence(*, activity: discord.BaseActivity) -> None:
            first_write.set()

        self.bot.change_presence.side_effect = presence
        self.ready.set()
        await asyncio.wait_for(first_write.wait(), timeout=1)
        override = await self.cog.set_override(self.fortnite, 60)
        self.now = override.deadline
        first_write.clear()
        self.cog._wake_event.set()

        await asyncio.wait_for(first_write.wait(), timeout=1)

        self.assertIsNone(self.cog.override)
        self.assertIn(self.cog.current_status, self.random_statuses)

    async def test_scheduler_expires_override_without_an_external_wakeup(self) -> None:
        first_rotation = asyncio.Event()
        resumed_rotation = asyncio.Event()
        writes: list[str] = []

        async def presence(*, activity: discord.BaseActivity) -> None:
            writes.append(activity.name)
            if len(writes) == 1:
                first_rotation.set()
            elif activity.name != "Fortnite":
                resumed_rotation.set()

        self.bot.change_presence.side_effect = presence
        with patch.object(status_module.time, "monotonic", side_effect=time.monotonic):
            self.ready.set()
            await asyncio.wait_for(first_rotation.wait(), timeout=1)
            # Shorten the internal timer; command duration bounds are tested separately.
            override = await self.cog.set_override(self.fortnite, 1)
            await asyncio.sleep(0)
            self.assertIs(self.cog.override, override)
            self.assertEqual(writes, ["first random", "Fortnite"])

            await asyncio.wait_for(resumed_rotation.wait(), timeout=3)

            self.assertIsNone(self.cog.override)
            self.assertEqual(writes, ["first random", "Fortnite", "second random"])
            self.assertFalse(self.cog.rotation_task.done())

    async def test_unload_cancels_rotation_and_fresh_cog_starts_without_override(self) -> None:
        await self.cog.set_override(self.fortnite, 60)
        await self.cog.cog_unload()
        self.assertTrue(self.cog.rotation_task.done())

        fresh = BotStatusCog(self.bot)
        try:
            self.assertIsNone(fresh.override)
            await fresh.update_status()
            self.assertIn(fresh.current_status, self.random_statuses)
        finally:
            await fresh.cog_unload()

    async def test_unload_completes_when_rotation_wakeup_resolves_at_the_same_time(self) -> None:
        first_rotation = asyncio.Event()

        async def presence(*, activity: discord.BaseActivity) -> None:
            first_rotation.set()

        self.bot.change_presence.side_effect = presence
        self.ready.set()
        await asyncio.wait_for(first_rotation.wait(), timeout=1)
        await asyncio.sleep(0)

        # Resolve the event waiter immediately before unload cancels rotation.
        self.cog._wake_event.set()
        unload = asyncio.create_task(self.cog.cog_unload())
        try:
            completed, _ = await asyncio.wait({unload}, timeout=1)
            self.assertIn(unload, completed, "Unload swallowed cancellation during wakeup")
            await unload
            self.assertTrue(self.cog.rotation_task.done())
        finally:
            # A regression must fail promptly, without leaving cleanup blocked.
            self.cog.rotation_task.cancel()
            if not unload.done():
                unload.cancel()
            completed, _ = await asyncio.wait(
                {unload, self.cog.rotation_task}, timeout=1,
            )
            await asyncio.gather(*completed, return_exceptions=True)

    async def test_unload_drains_manual_write_and_rejects_queued_and_later_mutations(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        writes: list[str] = []

        async def slow_presence(*, activity: discord.BaseActivity) -> None:
            if activity.name == "Fortnite":
                entered.set()
                await release.wait()
            writes.append(activity.name)

        self.bot.change_presence.side_effect = slow_presence
        manual = asyncio.create_task(self.cog.set_override(self.fortnite, 60))
        tasks = {manual}
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            queued_set = asyncio.create_task(
                self.cog.set_override(make_override_status("WATCHING", "queued movie"), 60)
            )
            queued_reset = asyncio.create_task(self.cog.reset_override())
            unload = asyncio.create_task(self.cog.cog_unload())
            tasks.update({queued_set, queued_reset, unload})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertFalse(unload.done(), "Unload must wait for the pending presence write")
            self.assertFalse(manual.done())
            self.assertEqual(writes, [])

            release.set()
            completed, pending = await asyncio.wait(tasks, timeout=1)
            self.assertFalse(pending, "Unload or queued status commands did not finish")
            await manual
            await unload
            for queued in (queued_set, queued_reset):
                with self.assertRaises(RuntimeError):
                    await queued
            self.assertEqual(writes, ["Fortnite"])

            fresh = BotStatusCog(self.bot)
            self.addAsyncCleanup(fresh.cog_unload)
            await fresh.update_status()
            for operation in (
                lambda: self.cog.set_override(self.fortnite, 60),
                self.cog.reset_override,
            ):
                with self.assertRaises(RuntimeError):
                    await operation()
            self.assertEqual(writes, ["Fortnite", "first random"])
            self.assertEqual(fresh.current_status, self.random_statuses[0])
            self.assertIsNone(fresh.override)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            completed, _ = await asyncio.wait(tasks, timeout=1)
            await asyncio.gather(*completed, return_exceptions=True)

    async def test_group_and_all_subcommands_require_guild_administrator(self) -> None:
        for command in (self.cog.status_group, *self.cog.status_group.commands):
            with self.subTest(command=command.qualified_name):
                self.assertTrue(
                    await discord.utils.async_all(check(self.make_context()) for check in command.checks)
                )
                with self.assertRaises(commands.MissingPermissions):
                    await discord.utils.async_all(
                        check(self.make_context(administrator=False)) for check in command.checks
                    )
                with self.assertRaises(commands.NoPrivateMessage):
                    await discord.utils.async_all(
                        check(self.make_context(guild_id=None)) for check in command.checks
                    )

    async def test_command_parser_preserves_unquoted_multiword_status(self) -> None:
        command = self.cog.set_bot_status
        ctx = self.make_context()
        ctx.message = SimpleNamespace(attachments=[])
        ctx.view = StringView("PLAYING 1h Fortnite Battle Royale")
        await command._parse_arguments(ctx)
        await command.callback(self.cog, *ctx.args, **ctx.kwargs)

        self.assertEqual(self.cog.override.status["text"], "Fortnite Battle Royale")
        self.assertEqual(self.cog.override.deadline, self.now + 3600)
        self.assert_mentions_disabled(ctx)

    async def test_invalid_command_input_returns_usage_without_changing_presence(self) -> None:
        for activity_type, duration, text in (
            ("SLEEPING", "1h", "Fortnite"),
            ("PLAYING", "25h", "Fortnite"),
            ("PLAYING", "1h", " "),
            ("PLAYING", "1h", "Fortnite\nBattle Royale"),
        ):
            with self.subTest(activity_type=activity_type, duration=duration, text=text):
                ctx = self.make_context()
                with self.assertRaises(commands.BadArgument) as raised:
                    await self.cog.set_bot_status.callback(
                        self.cog, ctx, activity_type, duration, text=text,
                    )
                await self.cog.cog_command_error(ctx, raised.exception)

                self.assertIn("bot_status set", str(ctx.send.await_args))
                self.assertNotIn("Đã đặt", str(ctx.send.await_args))
                self.assert_mentions_disabled(ctx)
                self.assertIsNone(self.cog.override)
                self.bot.change_presence.assert_not_awaited()

    async def test_missing_command_arguments_return_usage(self) -> None:
        for arguments in ("", "PLAYING", "PLAYING 1h"):
            with self.subTest(arguments=arguments):
                ctx = self.make_context()
                ctx.message = SimpleNamespace(attachments=[])
                ctx.view = StringView(arguments)
                with self.assertRaises(commands.MissingRequiredArgument) as raised:
                    await self.cog.set_bot_status._parse_arguments(ctx)
                await self.cog.cog_command_error(ctx, raised.exception)

                self.assertIn("Sai cú pháp", str(ctx.send.await_args))
                self.assertIn("bot_status set", str(ctx.send.await_args))
                self.assert_mentions_disabled(ctx)
                self.bot.change_presence.assert_not_awaited()

    async def test_group_and_show_display_override_expiry_and_usage_safely(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "@everyone *Fortnite*"), 60)
        for command in (self.cog.status_group, self.cog.show_bot_status):
            with self.subTest(command=command.qualified_name):
                ctx = self.make_context()
                await command.callback(self.cog, ctx)
                rendered = " ".join(
                    str(sent.args) + (
                        str(sent.kwargs["embed"].to_dict())
                        if sent.kwargs.get("embed") is not None else ""
                    )
                    for sent in ctx.send.await_args_list
                )
                self.assertIn("Fortnite", rendered)
                self.assertIn(str(int(self.cog.override.expires_at.timestamp())), rendered)
                self.assertIn("bot_status set", rendered)
                self.assert_mentions_disabled(ctx)

    async def test_group_tracks_panel_and_unload_stops_it(self) -> None:
        ctx = self.make_context()
        await self.cog.status_group.callback(self.cog, ctx)

        view = ctx.send.await_args.kwargs["view"]
        self.assertIn(view, self.cog._status_views)
        self.assertIs(view.message, ctx.send.return_value)
        self.assertFalse(view.is_finished())
        self.bot.change_presence.assert_not_awaited()

        await self.cog.cog_unload()

        self.assertTrue(view.is_finished())

    async def test_failed_panel_send_stops_view(self) -> None:
        ctx = self.make_context()
        ctx.send.side_effect = RuntimeError("message could not be sent")

        with self.assertRaises(RuntimeError):
            await self.cog.status_group.callback(self.cog, ctx)

        view = ctx.send.await_args.kwargs["view"]
        self.assertTrue(view.is_finished())
        self.bot.change_presence.assert_not_awaited()

    async def test_set_and_reset_share_cooldown_across_guilds(self) -> None:
        first_guild = self.make_context(guild_id=1)
        second_guild = self.make_context(guild_id=2)
        await self.cog.set_bot_status.callback(self.cog, first_guild, "PLAYING", "1h", text="Fortnite")

        with self.assertRaises(commands.CommandOnCooldown):
            await self.cog.reset_bot_status.callback(self.cog, second_guild)

        self.now += 11
        await self.cog.reset_bot_status.callback(self.cog, second_guild)
        self.assertIsNone(self.cog.override)
        with self.assertRaises(commands.CommandOnCooldown):
            await self.cog.set_bot_status.callback(
                self.cog, first_guild, "WATCHING", "1h", text="a movie",
            )
        self.assert_mentions_disabled(first_guild)
        self.assert_mentions_disabled(second_guild)

    async def test_manual_presence_failure_returns_safe_error_without_success(self) -> None:
        self.bot.change_presence.side_effect = RuntimeError("private gateway details")
        ctx = self.make_context()
        with self.assertRaises(RuntimeError) as raised:
            await self.cog.set_bot_status.callback(self.cog, ctx, "PLAYING", "1h", text="Fortnite")
        ctx.send.assert_not_awaited()
        with self.assertLogs(status_module.logger, level="ERROR"):
            await self.cog.cog_command_error(ctx, commands.CommandInvokeError(raised.exception))

        self.assertIsNone(self.cog.override)
        self.assert_mentions_disabled(ctx)
        rendered = str(ctx.send.await_args)
        self.assertNotIn("private gateway details", rendered)
        self.assertNotIn("Đã đặt", rendered)


if __name__ == "__main__":
    unittest.main()
