import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from cogs.mod._interaction_ui import ConfigurableModerationView
from cogs.mod.kick import KickCog, KickRequest
from cogs.mod.mute import MuteCog, MuteRequest
from cogs.mod.softban import SoftbanCog, SoftbanRequest
from cogs.mod.timeout import MAX_TIMEOUT_MINUTES, TimeoutCog, TimeoutRequest
from cogs.mod.warn import WarnCommandCog, WarnRequest


class FakeRole:
    def __init__(
        self,
        role_id: int,
        position: int,
        *,
        name: str,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.id = role_id
        self.position = position
        self.name = name
        self.managed = managed
        self._default = default
        self.mention = f"<@&{role_id}>"

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other: "FakeRole") -> bool:
        return self.position < other.position

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position

    def __ge__(self, other: "FakeRole") -> bool:
        return self.position >= other.position


class FakeMember:
    def __init__(
        self,
        guild: "FakeGuild",
        member_id: int,
        position: int,
        *,
        name: str,
        bot: bool = False,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.name = name
        self.bot = bot
        self.top_role = FakeRole(member_id, position, name=f"role-{name}")
        self.guild_permissions = SimpleNamespace(
            kick_members=True,
            ban_members=True,
            manage_roles=True,
            moderate_members=True,
            manage_messages=True,
        )
        self.roles: list[FakeRole] = []
        self.mention = f"<@{member_id}>"
        self.kick = AsyncMock()
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()
        self.edit = AsyncMock()
        self.timeout = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1000
        self.me: FakeMember | None = None
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, MagicMock] = {}

    def __getitem__(self, name: str) -> MagicMock:
        return self.collections.setdefault(name, MagicMock())


class FakeChannel:
    def __init__(self) -> None:
        self.id = 555
        self.fetch_message = AsyncMock()


def make_fixture() -> tuple[
    FakeGuild,
    FakeMember,
    FakeMember,
    FakeChannel,
    FakeRole,
    FakeRole,
]:
    guild = FakeGuild()
    everyone = FakeRole(1, 0, name="@everyone", default=True)
    original = FakeRole(2, 5, name="Member")
    muted = FakeRole(3, 6, name="Muted")
    handcuffed = FakeRole(4, 7, name="Tù ngay")
    guild.roles = [everyone, original, muted, handcuffed]
    bot_member = FakeMember(guild, 999, 100, name="mod-bot", bot=True)
    moderator = FakeMember(guild, 42, 80, name="moderator")
    target = FakeMember(guild, 77, 10, name="target")
    target.roles = [everyone, original]
    guild.me = bot_member
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        target.id: target,
    }
    guild.fetch_member.return_value = target
    return guild, moderator, target, FakeChannel(), muted, handcuffed


def make_context(
    guild: FakeGuild,
    moderator: FakeMember,
    channel: FakeChannel,
    *,
    reference: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=SimpleNamespace(reference=reference),
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        send=AsyncMock(),
    )


def make_reply_reference(
    guild: FakeGuild,
    channel: FakeChannel,
    target: FakeMember,
) -> SimpleNamespace:
    message = SimpleNamespace(
        id=123,
        guild=guild,
        channel=channel,
        author=target,
        webhook_id=None,
    )
    return SimpleNamespace(
        message_id=123,
        channel_id=channel.id,
        resolved=None,
        cached_message=message,
    )


def make_interaction(
    guild: FakeGuild,
    moderator: FakeMember,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=moderator,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def make_forbidden_exception() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        response,
        {"code": 50013, "message": "Missing Permissions"},
    )


class TestMemberModerationCommands(unittest.IsolatedAsyncioTestCase):
    async def test_reply_opens_each_member_workflow_without_mutating(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        reference = make_reply_reference(guild, channel, target)
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cases = (
            (KickCog(bot), KickCog.kick_member),
            (MuteCog(bot), MuteCog.mute_member),
            (MuteCog(bot), MuteCog.unmute_member),
            (TimeoutCog(bot), TimeoutCog.timeout),
            (TimeoutCog(bot), TimeoutCog.untimeout),
            (WarnCommandCog(bot), WarnCommandCog.warn_user),
            (SoftbanCog(bot), SoftbanCog.softban_member),
            (SoftbanCog(bot), SoftbanCog.unsoftban_member),
        )

        for cog, command in cases:
            with self.subTest(command=command.name):
                ctx = make_context(
                    guild,
                    moderator,
                    channel,
                    reference=reference,
                )
                await command.callback(cog, ctx)
                view = ctx.reply.await_args.kwargs["view"]
                self.assertIsInstance(view, ConfigurableModerationView)
                self.assertEqual(view.target.id, target.id)
                view.stop()

        target.kick.assert_not_awaited()
        target.add_roles.assert_not_awaited()
        target.remove_roles.assert_not_awaited()
        target.edit.assert_not_awaited()
        target.timeout.assert_not_awaited()

    async def test_reply_with_arguments_is_rejected(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        bot = SimpleNamespace(db=FakeDatabase())
        cases = (
            (KickCog(bot), KickCog.kick_member),
            (MuteCog(bot), MuteCog.mute_member),
            (MuteCog(bot), MuteCog.unmute_member),
            (TimeoutCog(bot), TimeoutCog.timeout),
            (TimeoutCog(bot), TimeoutCog.untimeout),
            (WarnCommandCog(bot), WarnCommandCog.warn_user),
            (SoftbanCog(bot), SoftbanCog.softban_member),
            (SoftbanCog(bot), SoftbanCog.unsoftban_member),
        )
        for cog, command in cases:
            with self.subTest(command=command.name):
                ctx = make_context(
                    guild,
                    moderator,
                    channel,
                    reference=make_reply_reference(guild, channel, target),
                )
                await command.callback(cog, ctx, target, reason="spam")

                self.assertNotIn("view", ctx.reply.await_args.kwargs)
                self.assertIn("không kèm đối số", ctx.reply.await_args.args[0])
        guild.fetch_member.assert_not_awaited()

    async def test_reply_kick_waits_for_confirmation(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        bot = SimpleNamespace()
        cog = KickCog(bot)
        ctx = make_context(
            guild,
            moderator,
            channel,
            reference=make_reply_reference(guild, channel, target),
        )

        await cog.kick_member.callback(cog, ctx)
        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.step, "reason")
        target.kick.assert_not_awaited()

        reason_select = next(
            item for item in view.children if isinstance(item, discord.ui.Select)
        )
        preset = view.spec.reason.presets[0]
        reason_select._values = [preset.key]
        await reason_select.callback(make_interaction(guild, moderator))
        self.assertEqual(view.step, "confirm")
        target.kick.assert_not_awaited()

        with patch(
            "cogs.mod.kick.record_case",
            new_callable=AsyncMock,
            return_value=12,
        ):
            await view.children[0].callback(make_interaction(guild, moderator))

        guild.fetch_member.assert_awaited_once_with(target.id)
        target.kick.assert_awaited_once_with(
            reason=f"{preset.reason} (Requested by moderator)"
        )
        self.assertTrue(view.completed)

    async def test_timeout_with_duration_runs_directly(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        cog = TimeoutCog(SimpleNamespace())
        ctx = make_context(guild, moderator, channel)
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)

        with (
            patch("cogs.mod.timeout.discord.utils.utcnow", return_value=now),
            patch("cogs.mod.timeout.record_case", new_callable=AsyncMock) as record_case,
        ):
            await cog.timeout.callback(cog, ctx, target, 90, reason="  repeated  spam  ")

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        target.timeout.assert_awaited_once_with(
            now + timedelta(minutes=90),
            reason="repeated spam (Requested by moderator)",
        )
        self.assertEqual(record_case.await_args.kwargs["duration_seconds"], 5400)

    async def test_timeout_without_duration_opens_workflow(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        cog = TimeoutCog(SimpleNamespace())
        ctx = make_context(guild, moderator, channel)

        await cog.timeout.callback(cog, ctx, target, reason="spam")

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.step, "field:duration_minutes")
        self.assertNotIn("duration_minutes", view.values)
        self.assertEqual(view.initial_reason, "spam")
        target.timeout.assert_not_awaited()
        view.stop()

    async def test_direct_timeout_rejects_out_of_range_duration(self) -> None:
        for duration in (0, -1, MAX_TIMEOUT_MINUTES + 1):
            with self.subTest(duration=duration):
                guild, moderator, target, channel, _, _ = make_fixture()
                cog = TimeoutCog(SimpleNamespace())
                ctx = make_context(guild, moderator, channel)

                with patch(
                    "cogs.mod.timeout.record_case", new_callable=AsyncMock
                ) as record_case:
                    await cog.timeout.callback(cog, ctx, target, duration)

                self.assertNotIn("view", ctx.reply.await_args.kwargs)
                self.assertIn("1 đến 40,320", ctx.reply.await_args.args[0])
                target.timeout.assert_not_awaited()
                record_case.assert_not_awaited()

    async def test_explicit_member_runs_legacy_action_and_preserves_reason(self) -> None:
        cases = (
            (KickCog, KickCog.kick_member, "kick", "Không có lý do cụ thể"),
            (MuteCog, MuteCog.mute_member, "add_roles", "Không có lý do cụ thể"),
            (MuteCog, MuteCog.unmute_member, "remove_roles", "Moderator removed mute"),
            (TimeoutCog, TimeoutCog.untimeout, "timeout", "Moderator removed timeout"),
            (WarnCommandCog, WarnCommandCog.warn_user, None, "Không có lý do cụ thể"),
            (SoftbanCog, SoftbanCog.softban_member, "edit", "Không có lý do cụ thể"),
            (SoftbanCog, SoftbanCog.unsoftban_member, "add_roles", "Moderator removed softban"),
        )
        for cog_type, command, method_name, default_reason in cases:
            for provided_reason in (None, "  repeated   spam  "):
                with self.subTest(command=command.name, reason=provided_reason):
                    guild, moderator, target, channel, muted, handcuffed = make_fixture()
                    database = FakeDatabase()
                    database["old_roles"].find_one.return_value = (
                        {"old_roles": [2]} if command.name == "unsoftban" else None
                    )
                    if command.name == "unmute":
                        target.roles.append(muted)
                    if command.name == "unsoftban":
                        target.roles.append(handcuffed)
                    cog = cog_type(SimpleNamespace(db=database))
                    ctx = make_context(guild, moderator, channel)

                    with patch(
                        f"{cog_type.__module__}.record_case",
                        new_callable=AsyncMock,
                        return_value=12,
                    ) as record_case:
                        await command.callback(cog, ctx, target, reason=provided_reason)

                    expected_reason = (
                        default_reason if provided_reason is None else "repeated spam"
                    )
                    guild.fetch_member.assert_awaited_once_with(target.id)
                    self.assertNotIn("view", ctx.reply.await_args.kwargs)
                    self.assertFalse(ctx.reply.await_args.kwargs["mention_author"])
                    self.assertEqual(
                        ctx.reply.await_args.kwargs["allowed_mentions"].to_dict(),
                        discord.AllowedMentions.none().to_dict(),
                    )
                    record_case.assert_awaited_once()
                    self.assertEqual(record_case.await_args.kwargs["action"], command.name)
                    self.assertEqual(record_case.await_args.kwargs["reason"], expected_reason)
                    if method_name is None:
                        document = database["warnings"].insert_one.call_args.args[0]
                        self.assertEqual(document["reason"], expected_reason)
                    else:
                        method = getattr(target, method_name)
                        method.assert_awaited_once()
                        self.assertEqual(
                            method.await_args.kwargs["reason"],
                            f"{expected_reason} (Requested by moderator)",
                        )

    async def test_direct_kick_rechecks_permissions_and_target_hierarchy(self) -> None:
        for blocked_by in ("permission", "hierarchy", "lock"):
            with self.subTest(blocked_by=blocked_by):
                guild, moderator, target, channel, _, _ = make_fixture()
                cog = KickCog(SimpleNamespace())
                ctx = make_context(guild, moderator, channel)
                if blocked_by == "permission":
                    moderator.guild_permissions.kick_members = False
                elif blocked_by == "hierarchy":
                    refreshed = FakeMember(guild, target.id, 101, name="promoted-target")
                    guild.fetch_member.return_value = refreshed
                else:
                    cog._active_targets.add((guild.id, target.id))

                with patch("cogs.mod.kick.record_case", new_callable=AsyncMock) as record_case:
                    await cog.kick_member.callback(cog, ctx, target)

                self.assertNotIn("view", ctx.reply.await_args.kwargs)
                target.kick.assert_not_awaited()
                guild.fetch_member.return_value.kick.assert_not_awaited()
                record_case.assert_not_awaited()

    async def test_live_permission_loss_blocks_workflow(self) -> None:
        guild, moderator, target, channel, _, _ = make_fixture()
        cog = KickCog(SimpleNamespace())
        ctx = make_context(
            guild,
            moderator,
            channel,
            reference=make_reply_reference(guild, channel, target),
        )
        await cog.kick_member.callback(cog, ctx)
        view = ctx.reply.await_args.kwargs["view"]
        moderator.guild_permissions.kick_members = False
        interaction = make_interaction(guild, moderator)

        self.assertFalse(await view.interaction_check(interaction))
        self.assertIn(
            "Kick Members",
            interaction.response.send_message.await_args.args[0],
        )
        view.stop()


class TestMemberModerationSubmission(unittest.IsolatedAsyncioTestCase):
    async def test_kick_refetches_and_rechecks_bot_hierarchy(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        target.top_role = FakeRole(50, 101, name="too-high")
        cog = KickCog(SimpleNamespace())

        with patch("cogs.mod.kick.record_case", new_callable=AsyncMock) as record_case:
            result = await cog._submit_kick(
                make_interaction(guild, moderator),
                KickRequest(target.id, "spam"),
            )

        guild.fetch_member.assert_awaited_once_with(target.id)
        self.assertFalse(result.completed)
        target.kick.assert_not_awaited()
        record_case.assert_not_awaited()

    async def test_mute_and_unmute_preserve_cases(self) -> None:
        guild, moderator, target, _, muted, _ = make_fixture()
        bot = SimpleNamespace()
        cog = MuteCog(bot)
        with patch(
            "cogs.mod.mute.record_case",
            new_callable=AsyncMock,
            side_effect=[3, 4],
        ) as record_case:
            muted_result = await cog._submit_mute(
                make_interaction(guild, moderator),
                MuteRequest(target.id, False, "spam"),
            )
            target.roles.append(muted)
            unmuted_result = await cog._submit_mute(
                make_interaction(guild, moderator),
                MuteRequest(target.id, True, "appeal"),
            )

        self.assertTrue(muted_result.completed)
        self.assertTrue(unmuted_result.completed)
        target.add_roles.assert_awaited_once()
        target.remove_roles.assert_awaited_once()
        self.assertEqual(
            [call.kwargs["action"] for call in record_case.await_args_list],
            ["mute", "unmute"],
        )

    async def test_timeout_and_untimeout_preserve_duration_and_cases(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        bot = SimpleNamespace()
        cog = TimeoutCog(bot)
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        with (
            patch("cogs.mod.timeout.discord.utils.utcnow", return_value=now),
            patch(
                "cogs.mod.timeout.record_case",
                new_callable=AsyncMock,
                side_effect=[7, 8],
            ) as record_case,
        ):
            timed = await cog._submit_timeout(
                make_interaction(guild, moderator),
                TimeoutRequest(target.id, 60, "spam"),
            )
            untimed = await cog._submit_timeout(
                make_interaction(guild, moderator),
                TimeoutRequest(target.id, None, "appeal"),
            )

        self.assertTrue(timed.completed)
        self.assertTrue(untimed.completed)
        self.assertEqual(target.timeout.await_args_list[0].args[0].hour, 1)
        self.assertIsNone(target.timeout.await_args_list[1].args[0])
        self.assertEqual(record_case.await_args_list[0].kwargs["duration_seconds"], 3600)
        self.assertNotIn("duration_seconds", record_case.await_args_list[1].kwargs)

    async def test_warn_writes_only_after_confirm_submit_and_records_case(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cog = WarnCommandCog(bot)
        with patch(
            "cogs.mod.warn.record_case",
            new_callable=AsyncMock,
            return_value=9,
        ) as record_case:
            result = await cog._submit_warn(
                make_interaction(guild, moderator),
                WarnRequest(target.id, "  repeated   spam  "),
            )

        document = database["warnings"].insert_one.call_args.args[0]
        self.assertEqual(document["user_id"], target.id)
        self.assertEqual(document["reason"], "repeated spam")
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_softban_preserves_role_snapshot_and_case(self) -> None:
        guild, moderator, target, _, _, handcuffed = make_fixture()
        database = FakeDatabase()
        bot = SimpleNamespace(db=database)
        cog = SoftbanCog(bot)
        database["old_roles"].find_one.return_value = None
        with patch(
            "cogs.mod.softban.record_case",
            new_callable=AsyncMock,
            return_value=11,
        ) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, False, "spam"),
            )

        snapshot = database["old_roles"].update_one.call_args.args[1]["$setOnInsert"]
        self.assertEqual(snapshot["old_roles"], [2])
        target.edit.assert_awaited_once()
        target.add_roles.assert_awaited_once_with(
            handcuffed,
            reason="spam (Requested by moderator)",
        )
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_softban_handcuff_failure_rolls_roles_back(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = None
        target.add_roles.side_effect = [make_forbidden_exception(), None]

        with patch("cogs.mod.softban.record_case", new_callable=AsyncMock) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, False, "spam"),
            )

        self.assertFalse(result.completed)
        self.assertEqual(target.add_roles.await_count, 2)
        rollback_roles = target.add_roles.await_args_list[1].args
        self.assertEqual([role.id for role in rollback_roles], [2])
        database["old_roles"].delete_one.assert_called_once()
        record_case.assert_not_awaited()

    async def test_softban_retry_never_overwrites_existing_role_snapshot(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = {"old_roles": [2]}
        target.roles = [guild.roles[0]]
        target.add_roles.side_effect = make_forbidden_exception()

        result = await cog._submit_softban(
            make_interaction(guild, moderator),
            SoftbanRequest(target.id, False, "retry"),
        )

        self.assertFalse(result.completed)
        database["old_roles"].update_one.assert_not_called()
        database["old_roles"].delete_one.assert_not_called()

    async def test_unsoftban_restores_saved_roles_and_records_case(self) -> None:
        guild, moderator, target, _, _, handcuffed = make_fixture()
        database = FakeDatabase()
        cog = SoftbanCog(SimpleNamespace(db=database))
        database["old_roles"].find_one.return_value = {"old_roles": [2]}
        target.roles.append(handcuffed)
        with patch(
            "cogs.mod.softban.record_case",
            new_callable=AsyncMock,
            return_value=13,
        ) as record_case:
            result = await cog._submit_softban(
                make_interaction(guild, moderator),
                SoftbanRequest(target.id, True, "appeal"),
            )

        target.remove_roles.assert_awaited_once()
        self.assertEqual(target.add_roles.await_args.args[0].id, 2)
        database["old_roles"].delete_one.assert_called_once()
        record_case.assert_awaited_once()
        self.assertTrue(result.completed)

    async def test_per_target_lock_rejects_parallel_submit(self) -> None:
        guild, moderator, target, _, _, _ = make_fixture()
        cog = KickCog(SimpleNamespace())
        cog._active_targets.add((guild.id, target.id))

        result = await cog._submit_kick(
            make_interaction(guild, moderator),
            KickRequest(target.id, "spam"),
        )

        self.assertFalse(result.completed)
        guild.fetch_member.assert_not_awaited()

    def test_stateful_commands_use_five_second_member_cooldowns(self) -> None:
        command_objects = (
            KickCog.kick_member,
            MuteCog.mute_member,
            MuteCog.unmute_member,
            TimeoutCog.timeout,
            TimeoutCog.untimeout,
            WarnCommandCog.warn_user,
            SoftbanCog.softban_member,
            SoftbanCog.unsoftban_member,
        )
        for command in command_objects:
            with self.subTest(command=command.qualified_name):
                self.assertEqual(command._buckets._cooldown.per, 5.0)
                self.assertIs(command._buckets.type, commands.BucketType.member)


if __name__ == "__main__":
    unittest.main()
