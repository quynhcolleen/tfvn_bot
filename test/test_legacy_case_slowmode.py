import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs.mod.cases import ModerationCasesCog
from cogs.mod.slowmode import SlowmodeCog


def make_fixture():
    guild = SimpleNamespace(id=10, owner_id=1_000)
    moderator = SimpleNamespace(
        id=42,
        guild=guild,
        top_role=80,
        guild_permissions=SimpleNamespace(manage_messages=True, manage_guild=True),
    )
    target = SimpleNamespace(id=77, guild=guild, top_role=10)
    guild.me = SimpleNamespace(id=999, guild=guild, top_role=100)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 555
    channel.name = "moderation"
    channel.guild = guild
    channel.type = discord.ChannelType.text
    channel.permissions_for.return_value = SimpleNamespace(
        manage_roles=True,
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )
    channel.overwrites_for.return_value = discord.PermissionOverwrite()
    channel.set_permissions = AsyncMock()
    guild.get_channel = lambda channel_id: channel if channel_id == channel.id else None
    guild.get_member = lambda member_id: target if member_id == target.id else None
    ctx = SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        clean_prefix="!tf ",
        message=SimpleNamespace(reference=None),
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        send=AsyncMock(),
    )
    return guild, moderator, target, channel, ctx


class TestLegacyCases(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        cog = object.__new__(ModerationCasesCog)
        cog.cases = MagicMock()
        cog.config = MagicMock()
        cog._send_case_log = AsyncMock()
        cog.cases.find_one.return_value = {
            "case_number": 7,
            "reason": "old reason",
            "status": "open",
            "updated_at": object(),
        }
        return cog

    async def test_explicit_reason_updates_case_and_records_editor(self) -> None:
        guild, moderator, _, _, ctx = make_fixture()
        cog = self.make_cog()
        snapshot = cog.cases.find_one.return_value

        await cog.case_edit.callback(cog, ctx, 7, reason="  new   reason  ")

        query, update = cog.cases.find_one_and_update.call_args.args
        self.assertEqual(query["guild_id"], guild.id)
        self.assertEqual(query["case_number"], 7)
        self.assertEqual(query["reason"], "old reason")
        self.assertIs(query["updated_at"], snapshot["updated_at"])
        self.assertEqual(update["$set"]["reason"], "new reason")
        self.assertEqual(update["$set"]["updated_by"], moderator.id)
        self.assertEqual(update["$push"]["edit_history"]["old_value"], "old reason")
        cog._send_case_log.assert_awaited_once()
        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertFalse(ctx.reply.await_args.kwargs["mention_author"])
        self.assertEqual(
            ctx.reply.await_args.kwargs["allowed_mentions"].to_dict(),
            discord.AllowedMentions.none().to_dict(),
        )

    async def test_explicit_status_is_normalized_and_updates_atomically(self) -> None:
        guild, moderator, _, _, ctx = make_fixture()
        cog = self.make_cog()
        snapshot = cog.cases.find_one.return_value

        await cog.case_status.callback(cog, ctx, 7, " RESOLVED ")

        query, update = cog.cases.find_one_and_update.call_args.args
        self.assertEqual(query["guild_id"], guild.id)
        self.assertEqual(query["status"], "open")
        self.assertIs(query["updated_at"], snapshot["updated_at"])
        self.assertEqual(update["$set"]["status"], "resolved")
        self.assertEqual(update["$push"]["edit_history"]["editor_id"], moderator.id)
        cog._send_case_log.assert_awaited_once()
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_explicit_edit_reports_stale_update_without_logging(self) -> None:
        _, _, _, _, ctx = make_fixture()
        cog = self.make_cog()
        cog.cases.find_one_and_update.return_value = None

        await cog.case_edit.callback(cog, ctx, 7, reason="new reason")

        cog._send_case_log.assert_not_awaited()
        self.assertIn("đã thay đổi", ctx.reply.await_args.args[0])

    async def test_invalid_case_numbers_do_not_query_or_mutate(self) -> None:
        for command_name in ("case_edit", "case_status"):
            with self.subTest(command=command_name):
                _, _, _, _, ctx = make_fixture()
                cog = self.make_cog()

                await getattr(cog, command_name).callback(cog, ctx, 0)

                cog.cases.find_one.assert_not_called()
                cog.cases.find_one_and_update.assert_not_called()
                ctx.send.assert_awaited_once()

    async def test_invalid_status_does_not_update_case(self) -> None:
        _, _, _, _, ctx = make_fixture()
        cog = self.make_cog()

        await cog.case_status.callback(cog, ctx, 7, "deleted")

        cog.cases.find_one_and_update.assert_not_called()
        self.assertIn("Trạng thái phải", ctx.send.await_args.args[0])
        ctx.reply.assert_not_awaited()

    async def test_direct_case_edit_checks_current_permissions(self) -> None:
        _, moderator, _, _, ctx = make_fixture()
        moderator.guild_permissions.manage_messages = False
        cog = self.make_cog()

        await cog.case_edit.callback(cog, ctx, 7, reason="new reason")

        cog.cases.find_one_and_update.assert_not_called()
        self.assertIn("Manage Messages", ctx.reply.await_args.args[0])

    async def test_explicit_log_channel_updates_configuration(self) -> None:
        guild, moderator, _, channel, ctx = make_fixture()
        cog = self.make_cog()

        await cog.case_log_channel.callback(cog, ctx, channel)

        query, update = cog.config.update_one.call_args.args
        self.assertEqual(query, {"guild_id": guild.id})
        self.assertEqual(update["$set"]["log_channel_id"], channel.id)
        self.assertEqual(update["$set"]["updated_by"], moderator.id)
        self.assertTrue(cog.config.update_one.call_args.kwargs["upsert"])
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_direct_log_channel_requires_bot_embed_permission(self) -> None:
        _, _, _, channel, ctx = make_fixture()
        channel.permissions_for.return_value.embed_links = False
        cog = self.make_cog()

        await cog.case_log_channel.callback(cog, ctx, channel)

        cog.config.update_one.assert_not_called()
        self.assertIn("Embed Links", ctx.reply.await_args.args[0])

    async def test_missing_log_channel_opens_ui_without_writing(self) -> None:
        _, _, _, _, ctx = make_fixture()
        cog = self.make_cog()

        await cog.case_log_channel.callback(cog, ctx)

        cog.config.update_one.assert_not_called()
        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.step, "field:channel_id")
        view.stop()


class TestLegacySlowmode(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_target_changes_only_bypass_overwrite(self) -> None:
        for immune in (True, False):
            with self.subTest(immune=immune):
                _, _, target, channel, ctx = make_fixture()
                channel.overwrites_for.return_value = discord.PermissionOverwrite(
                    send_messages=False,
                    bypass_slowmode=None if immune else True,
                )
                cog = SlowmodeCog(SimpleNamespace())
                command = cog.slowmode_immune if immune else cog.slowmode_prominent

                await command.callback(cog, ctx, target, reason="  approved  bypass ")

                channel.set_permissions.assert_awaited_once()
                self.assertIs(channel.set_permissions.await_args.args[0], target)
                kwargs = channel.set_permissions.await_args.kwargs
                self.assertIs(kwargs["overwrite"].send_messages, False)
                self.assertIs(kwargs["overwrite"].bypass_slowmode, True if immune else None)
                self.assertTrue(kwargs["reason"].startswith("approved bypass"))
                self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_explicit_target_without_reason_uses_default(self) -> None:
        _, _, target, channel, ctx = make_fixture()
        cog = SlowmodeCog(SimpleNamespace())

        await cog.slowmode_immune.callback(cog, ctx, target)

        self.assertIn(
            "Không có lý do cụ thể",
            channel.set_permissions.await_args.kwargs["reason"],
        )

    async def test_argument_free_reply_opens_ui_for_each_action(self) -> None:
        for command_name in ("slowmode_immune", "slowmode_prominent"):
            with self.subTest(command=command_name):
                _, _, target, channel, ctx = make_fixture()
                ctx.message.reference = object()
                cog = SlowmodeCog(SimpleNamespace())
                with patch(
                    "cogs.mod.slowmode.resolve_same_channel_reply_member",
                    new=AsyncMock(return_value=target),
                ):
                    await getattr(cog, command_name).callback(cog, ctx)

                channel.set_permissions.assert_not_awaited()
                view = ctx.reply.await_args.kwargs["view"]
                self.assertEqual(view.target.id, target.id)
                self.assertEqual(view.step, "reason")
                view.stop()

    async def test_reply_with_explicit_target_is_rejected(self) -> None:
        _, _, target, channel, ctx = make_fixture()
        ctx.message.reference = object()
        cog = SlowmodeCog(SimpleNamespace())

        await cog.slowmode_immune.callback(cog, ctx, target)

        channel.set_permissions.assert_not_awaited()
        self.assertIn("Khi dùng bằng reply", ctx.reply.await_args.args[0])

    async def test_direct_override_preserves_hierarchy_check(self) -> None:
        _, moderator, target, channel, ctx = make_fixture()
        target.top_role = moderator.top_role
        cog = SlowmodeCog(SimpleNamespace())

        await cog.slowmode_immune.callback(cog, ctx, target)

        channel.set_permissions.assert_not_awaited()
        self.assertIn("role ngang/cao hơn", ctx.reply.await_args.args[0])

    async def test_direct_override_from_thread_updates_text_parent(self) -> None:
        guild, _, target, channel, ctx = make_fixture()
        thread = SimpleNamespace(
            id=556,
            guild=guild,
            type=discord.ChannelType.public_thread,
            parent=channel,
        )
        ctx.channel = thread
        cog = SlowmodeCog(SimpleNamespace())

        await cog.slowmode_immune.callback(cog, ctx, target)

        channel.set_permissions.assert_awaited_once()
        self.assertIn("#moderation", ctx.reply.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
