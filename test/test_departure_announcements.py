import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import discord

from cogs.announcement.goodbye import (
    AUDIT_LOOKUP_DELAY_SECONDS,
    DEPARTURE_SIGNAL_WINDOW_SECONDS,
    EVENT_GRACE_SECONDS,
    DepartureKind,
    GoodbyeCog,
    build_departure_embed,
)


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
BYE_CHANNEL_ID = 123
GUILD_ID = 456
MEMBER_ID = 789


def async_entries(entries):
    async def iterator():
        for entry in entries:
            yield entry

    return iterator()


def forbidden_entries():
    async def iterator():
        response = SimpleNamespace(status=403, reason="Forbidden")
        raise discord.Forbidden(
            response,
            {"code": 50013, "message": "Missing Permissions"},
        )
        yield

    return iterator()


def make_audit_entry(
    target_id: int,
    created_at: datetime,
) -> SimpleNamespace:
    return SimpleNamespace(
        action=discord.AuditLogAction.kick,
        target=SimpleNamespace(id=target_id),
        created_at=created_at,
    )


def make_fixture(*, view_audit_log: bool = True, channel_available=True):
    channel = SimpleNamespace(send=AsyncMock())
    guild = SimpleNamespace(
        id=GUILD_ID,
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                view_audit_log=view_audit_log,
            )
        ),
        audit_logs=Mock(),
    )
    member = SimpleNamespace(
        id=MEMBER_ID,
        name="Kien",
        display_avatar=SimpleNamespace(
            url="https://cdn.example/avatar.png",
        ),
        guild=guild,
    )
    channel.guild = guild
    bot = SimpleNamespace(
        guild_vars={GUILD_ID: {"BYE_CHANNEL": str(BYE_CHANNEL_ID)}},
        get_channel=Mock(
            return_value=channel if channel_available else None,
        ),
    )
    return GoodbyeCog(bot), bot, guild, member, channel


class TestDepartureEmbeds(unittest.TestCase):
    def test_each_departure_kind_has_distinct_copy(self) -> None:
        member = SimpleNamespace(
            name="Kien",
            display_avatar=SimpleNamespace(
                url="https://cdn.example/avatar.png",
            ),
        )
        expected_titles = {
            DepartureKind.LEAVE: "Kien đã rời khỏi server 🥹",
            DepartureKind.KICK: "Kien đã ăn kick và cút 👢",
            DepartureKind.BAN: "Kien đã ăn sút và cút 🔨",
            DepartureKind.UNKNOWN: "Kien đã rời hoặc bị đưa khỏi server 👋",
        }

        actual_titles = set()
        for kind, expected_title in expected_titles.items():
            with self.subTest(kind=kind):
                embed = build_departure_embed(member, kind)
                self.assertEqual(embed.title, expected_title)
                actual_titles.add(embed.title)

        self.assertEqual(len(actual_titles), len(expected_titles))

    def test_missing_bye_channel_setting_does_not_block_cog_loading(self) -> None:
        bot = SimpleNamespace(guild_vars={})
        self.assertIsInstance(GoodbyeCog(bot), GoodbyeCog)


class TestDepartureListeners(unittest.IsolatedAsyncioTestCase):
    async def test_another_guild_setting_does_not_enable_announcements(self) -> None:
        cog, bot, guild, member, channel = make_fixture()
        bot.guild_vars = {GUILD_ID + 1: {"BYE_CHANNEL": BYE_CHANNEL_ID}}
        await cog.on_member_remove(member)
        bot.get_channel.assert_not_called()
        guild.audit_logs.assert_not_called()
        channel.send.assert_not_awaited()

    async def test_channel_in_another_guild_is_not_used(self) -> None:
        cog, bot, guild, member, channel = make_fixture()
        channel.guild = SimpleNamespace(id=GUILD_ID + 1)
        with self.assertLogs("cogs.announcement.goodbye", level="WARNING"):
            await cog.on_member_remove(member)
        guild.audit_logs.assert_not_called()
        channel.send.assert_not_awaited()

    async def test_changed_setting_is_resolved_on_next_departure(self) -> None:
        cog, bot, guild, member, channel = make_fixture()
        bot.guild_vars[GUILD_ID]["BYE_CHANNEL"] = BYE_CHANNEL_ID + 1
        cog._classify_departure = AsyncMock(return_value=DepartureKind.LEAVE)
        await cog.on_member_remove(member)
        bot.get_channel.assert_called_once_with(BYE_CHANNEL_ID + 1)
        channel.send.assert_awaited_once()

    @staticmethod
    def sent_embed(channel) -> discord.Embed:
        channel.send.assert_awaited_once()
        return channel.send.await_args.kwargs["embed"]

    async def test_voluntary_leave_after_available_audit_checks(self) -> None:
        cog, bot, guild, member, channel = make_fixture()
        guild.audit_logs.side_effect = lambda **_kwargs: async_entries(())

        with (
            patch(
                "cogs.announcement.goodbye.discord.utils.utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
        ):
            await cog.on_member_remove(member)

        self.assertEqual(
            self.sent_embed(channel).title,
            "Kien đã rời khỏi server 🥹",
        )
        bot.get_channel.assert_called_once_with(BYE_CHANNEL_ID)
        self.assertEqual(guild.audit_logs.call_count, 2)
        sleep.assert_has_awaits(
            [
                call(EVENT_GRACE_SECONDS),
                call(AUDIT_LOOKUP_DELAY_SECONDS),
            ]
        )

    async def test_kick_audit_retries_and_ignores_wrong_or_stale_targets(
        self,
    ) -> None:
        cog, _bot, guild, member, channel = make_fixture()
        stale_time = NOW - timedelta(
            seconds=DEPARTURE_SIGNAL_WINDOW_SECONDS + 1,
        )
        batches = iter(
            [
                [
                    make_audit_entry(MEMBER_ID + 1, NOW),
                    make_audit_entry(MEMBER_ID, stale_time),
                ],
                [make_audit_entry(MEMBER_ID, NOW - timedelta(seconds=1))],
            ]
        )
        guild.audit_logs.side_effect = (
            lambda **_kwargs: async_entries(next(batches))
        )

        with (
            patch(
                "cogs.announcement.goodbye.discord.utils.utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
        ):
            await cog.on_member_remove(member)

        self.assertEqual(
            self.sent_embed(channel).title,
            "Kien đã ăn kick và cút 👢",
        )
        self.assertEqual(guild.audit_logs.call_count, 2)
        for audit_call in guild.audit_logs.call_args_list:
            self.assertEqual(
                audit_call.kwargs,
                {
                    "limit": 5,
                    "action": discord.AuditLogAction.kick,
                    "after": NOW
                    - timedelta(seconds=DEPARTURE_SIGNAL_WINDOW_SECONDS),
                    "oldest_first": False,
                },
            )
        sleep.assert_has_awaits(
            [
                call(EVENT_GRACE_SECONDS),
                call(AUDIT_LOOKUP_DELAY_SECONDS),
            ]
        )

    async def test_ban_signal_then_remove_emits_exactly_once(self) -> None:
        cog, _bot, guild, member, channel = make_fixture()

        with (
            patch(
                "cogs.announcement.goodbye.discord.utils.utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
        ):
            await cog.on_member_ban(guild, member)
            await cog.on_member_remove(member)

        self.assertEqual(
            self.sent_embed(channel).title,
            "Kien đã ăn sút và cút 🔨",
        )
        self.assertEqual(channel.send.await_count, 1)
        guild.audit_logs.assert_not_called()
        sleep.assert_not_awaited()

    async def test_missing_view_audit_log_uses_neutral_fallback(self) -> None:
        cog, _bot, guild, member, channel = make_fixture(
            view_audit_log=False,
        )

        with (
            patch(
                "cogs.announcement.goodbye.discord.utils.utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
        ):
            await cog.on_member_remove(member)

        self.assertEqual(
            self.sent_embed(channel).title,
            "Kien đã rời hoặc bị đưa khỏi server 👋",
        )
        guild.audit_logs.assert_not_called()
        sleep.assert_awaited_once_with(EVENT_GRACE_SECONDS)

    async def test_forbidden_audit_lookup_uses_neutral_fallback(self) -> None:
        cog, _bot, guild, member, channel = make_fixture()
        guild.audit_logs.return_value = forbidden_entries()

        with (
            patch(
                "cogs.announcement.goodbye.discord.utils.utcnow",
                return_value=NOW,
            ),
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
            patch("cogs.announcement.goodbye.logger.warning") as warning,
        ):
            await cog.on_member_remove(member)

        self.assertEqual(
            self.sent_embed(channel).title,
            "Kien đã rời hoặc bị đưa khỏi server 👋",
        )
        guild.audit_logs.assert_called_once()
        sleep.assert_awaited_once_with(EVENT_GRACE_SECONDS)
        warning.assert_called_once()

    async def test_unavailable_bye_channel_skips_classification(self) -> None:
        cog, bot, guild, member, channel = make_fixture(
            channel_available=False,
        )

        with (
            patch(
                "cogs.announcement.goodbye.asyncio.sleep",
                new_callable=AsyncMock,
            ) as sleep,
            patch("cogs.announcement.goodbye.logger.warning") as warning,
        ):
            await cog.on_member_remove(member)

        bot.get_channel.assert_called_once_with(BYE_CHANNEL_ID)
        channel.send.assert_not_awaited()
        guild.audit_logs.assert_not_called()
        sleep.assert_not_awaited()
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
