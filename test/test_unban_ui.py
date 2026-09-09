import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.mod._unban_ui import (
    REINVITE_MAX_AGE_SECONDS,
    UNBAN_UI_TIMEOUT_SECONDS,
    CustomReasonModal,
    UnbanActionResult,
    UnbanRequest,
    UnbanWorkflowView,
)
from cogs.mod.unban import UnbanCog, parse_unban_user_id


class FakeMember:
    def __init__(
        self,
        guild: "FakeGuild",
        member_id: int,
        *,
        name: str,
        can_ban: bool = True,
        can_invite: bool = True,
        can_view: bool = True,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.name = name
        self.guild_permissions = SimpleNamespace(ban_members=can_ban)
        self.can_invite = can_invite
        self.can_view = can_view
        self.send = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeUser:
    def __init__(
        self,
        user_id: int,
        *,
        name: str = "banned-user",
        bot: bool = False,
    ) -> None:
        self.id = user_id
        self.name = name
        self.bot = bot
        self.send = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self, guild_id: int = 10) -> None:
        self.id = guild_id
        self.name = "Test Guild"
        self.me: FakeMember | None = None
        self.default_role: FakeMember | None = None
        self.system_channel = None
        self.fetch_ban = AsyncMock()
        self.unban = AsyncMock()


class FakeInvite:
    def __init__(self) -> None:
        self.url = "https://discord.gg/one-use"
        self.delete = AsyncMock()


class FakeChannel:
    def __init__(self, channel_id: int = 555, *, public: bool = True) -> None:
        self.id = channel_id
        self.name = f"channel-{channel_id}"
        self.public = public
        self.fetch_message = AsyncMock()
        self.create_invite = AsyncMock(return_value=FakeInvite())

    def permissions_for(self, member: FakeMember) -> SimpleNamespace:
        return SimpleNamespace(
            create_instant_invite=member.can_invite,
            view_channel=self.public and member.can_view,
        )


def make_fixture() -> tuple[
    FakeGuild,
    FakeMember,
    FakeUser,
    FakeChannel,
]:
    guild = FakeGuild()
    guild.me = FakeMember(guild, 999, name="unban-bot")
    guild.default_role = FakeMember(guild, guild.id, name="@everyone")
    moderator = FakeMember(guild, 42, name="moderator")
    target = FakeUser(77)
    channel = FakeChannel()
    guild.fetch_ban.return_value = SimpleNamespace(user=target, reason="Old ban")
    return guild, moderator, target, channel


def make_interaction(
    guild: FakeGuild | None,
    user: FakeMember,
    channel: FakeChannel | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=user,
        channel=channel,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


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
        clean_prefix="!tf ",
        message=SimpleNamespace(reference=reference),
        reply=AsyncMock(
            return_value=SimpleNamespace(id=123, edit=AsyncMock())
        ),
    )


def make_forbidden_exception() -> discord.Forbidden:
    response = SimpleNamespace(status=403, reason="Forbidden")
    return discord.Forbidden(
        response,
        {"code": 50013, "message": "Missing Permissions"},
    )


def make_not_found_exception() -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(
        response,
        {"code": 10026, "message": "Unknown Ban"},
    )


class TestUnbanTargetParsing(unittest.TestCase):
    def test_parses_ids_and_mentions(self) -> None:
        self.assertEqual(parse_unban_user_id(" 77 "), 77)
        self.assertEqual(parse_unban_user_id("<@77>"), 77)
        self.assertEqual(parse_unban_user_id("<@!77>"), 77)
        self.assertEqual(
            parse_unban_user_id(str((1 << 64) - 1)),
            (1 << 64) - 1,
        )

    def test_rejects_invalid_snowflakes(self) -> None:
        for value in ("", " ", "zero", "-1", "0", "<@abc>", str(1 << 64)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_unban_user_id(value)


class TestUnbanWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_reinvite_preset_and_confirm_complete_three_steps(self) -> None:
        guild, moderator, target, channel = make_fixture()
        submitter = AsyncMock(
            return_value=UnbanActionResult(True, "Unban completed")
        )
        view = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=submitter,
        )
        self.assertEqual(view.timeout, UNBAN_UI_TIMEOUT_SECONDS)
        self.assertEqual(view.step, "reinvite")
        self.assertEqual(view.children, [view.reinvite_select, view.cancel_button])

        reinvite_interaction = make_interaction(guild, moderator, channel)
        view.reinvite_select._values = ["yes"]
        await view.reinvite_select.callback(reinvite_interaction)

        self.assertTrue(view.reinvite)
        self.assertEqual(view.step, "reason")
        self.assertEqual(
            view.children,
            [
                view.reason_select,
                view.custom_reason_button,
                view.back_button,
                view.cancel_button,
            ],
        )

        reason_interaction = make_interaction(guild, moderator, channel)
        view.reason_select._values = ["appeal"]
        await view.reason_select.callback(reason_interaction)

        self.assertEqual(view.reason, "Chấp nhận kháng nghị của thành viên")
        self.assertEqual(view.step, "confirm")
        self.assertEqual(view.children, [view.confirm_button, view.cancel_button])

        confirm_interaction = make_interaction(guild, moderator, channel)
        await view.confirm_button.callback(confirm_interaction)

        submitter.assert_awaited_once_with(
            confirm_interaction,
            UnbanRequest(
                target_id=target.id,
                reinvite=True,
                reason="Chấp nhận kháng nghị của thành viên",
            ),
        )
        confirm_interaction.response.defer.assert_awaited_once_with()
        confirm_interaction.edit_original_response.assert_awaited_once()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_no_reinvite_and_custom_reason_modal(self) -> None:
        guild, moderator, target, channel = make_fixture()
        view = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )

        reinvite_interaction = make_interaction(guild, moderator, channel)
        view.reinvite_select._values = ["no"]
        await view.reinvite_select.callback(reinvite_interaction)
        self.assertFalse(view.reinvite)

        open_modal = make_interaction(guild, moderator, channel)
        await view.custom_reason_button.callback(open_modal)
        modal = open_modal.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, CustomReasonModal)
        modal.reason._value = "  reviewed   and approved  "

        submit_modal = make_interaction(guild, moderator, channel)
        await modal.on_submit(submit_modal)
        self.assertEqual(view.reason, "reviewed and approved")
        self.assertEqual(view.step, "confirm")
        submit_modal.response.edit_message.assert_awaited_once()
        view.stop()

    async def test_cancel_and_timeout_disable_without_submitting(self) -> None:
        guild, moderator, target, channel = make_fixture()
        submitter = AsyncMock()
        cancelled = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=submitter,
        )
        cancel_interaction = make_interaction(guild, moderator, channel)

        await cancelled.cancel_button.callback(cancel_interaction)

        submitter.assert_not_awaited()
        self.assertTrue(cancelled.completed)
        self.assertTrue(cancelled.is_finished())
        self.assertTrue(all(item.disabled for item in cancelled.children))
        self.assertIn(
            "Đã hủy unban",
            cancel_interaction.response.edit_message.await_args.kwargs["content"],
        )

        expired = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )
        expired.message = SimpleNamespace(edit=AsyncMock())
        await expired.on_timeout()
        self.assertTrue(all(item.disabled for item in expired.children))
        expired.message.edit.assert_awaited_once_with(view=expired)
        expired.stop()

    async def test_owner_lock_and_live_permission_checks(self) -> None:
        guild, moderator, target, channel = make_fixture()
        view = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )
        self.assertTrue(
            await view.interaction_check(
                make_interaction(guild, moderator, channel)
            )
        )

        stranger = FakeMember(guild, 43, name="other-moderator")
        stranger_interaction = make_interaction(guild, stranger, channel)
        self.assertFalse(await view.interaction_check(stranger_interaction))
        self.assertIn(
            "Chỉ moderator",
            stranger_interaction.response.send_message.await_args.args[0],
        )

        moderator.guild_permissions.ban_members = False
        lost_permission = make_interaction(guild, moderator, channel)
        self.assertFalse(await view.interaction_check(lost_permission))
        self.assertIn(
            "không còn quyền Ban Members",
            lost_permission.response.send_message.await_args.args[0],
        )

        moderator.guild_permissions.ban_members = True
        guild.me.guild_permissions.ban_members = False
        bot_lost_permission = make_interaction(guild, moderator, channel)
        self.assertFalse(await view.interaction_check(bot_lost_permission))
        self.assertIn(
            "Bot không có quyền Ban Members",
            bot_lost_permission.response.send_message.await_args.args[0],
        )
        view.stop()

    async def test_private_completion_message_is_ephemeral(self) -> None:
        guild, moderator, target, channel = make_fixture()
        view = UnbanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(
                return_value=UnbanActionResult(
                    True,
                    "Public result",
                    private_message="Private invite",
                )
            ),
        )
        view.reinvite = True
        view.reason = "Appeal accepted"
        view._show_confirm_step()
        interaction = make_interaction(guild, moderator, channel)

        await view.confirm_button.callback(interaction)

        interaction.followup.send.assert_awaited_once()
        args = interaction.followup.send.await_args.args
        kwargs = interaction.followup.send.await_args.kwargs
        self.assertEqual(args[0], "Private invite")
        self.assertTrue(kwargs["ephemeral"])
        public_content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertNotIn("Private invite", public_content)


class TestUnbanCommandDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_reinvite_prefers_configured_public_channel(self) -> None:
        guild, moderator, _, current_channel = make_fixture()
        current_channel.public = False
        rules_channel = FakeChannel(777, public=True)
        guild.get_channel = lambda channel_id: (
            rules_channel if channel_id == rules_channel.id else None
        )
        ctx = make_context(guild, moderator, current_channel)
        cog = UnbanCog(
            SimpleNamespace(global_vars={"RULE_CHANNEL": str(rules_channel.id)})
        )

        with patch.object(
            cog,
            "_as_invite_channel",
            side_effect=lambda channel: channel,
        ):
            selected = cog._invite_channel(ctx)

        self.assertIs(selected, rules_channel)

    async def test_explicit_id_unbans_without_ui_or_reinvite(self) -> None:
        guild, moderator, target, channel = make_fixture()
        ctx = make_context(guild, moderator, channel)
        cog = UnbanCog(SimpleNamespace())

        with patch("cogs.mod.unban.record_case", new=AsyncMock(return_value=8)):
            await cog.unban_user.callback(
                cog,
                ctx,
                str(target.id),
                reason="  appeal   accepted  ",
            )

        lookup = guild.fetch_ban.await_args.args[0]
        self.assertEqual(lookup.id, target.id)
        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        guild.unban.assert_awaited_once_with(
            target, reason="appeal accepted (Requested by moderator)",
        )
        channel.create_invite.assert_not_awaited()
        target.send.assert_not_awaited()
        self.assertIn("Case #8", ctx.reply.await_args.args[0])

    async def test_invalid_direct_id_does_not_fetch_or_unban(self) -> None:
        guild, moderator, _, channel = make_fixture()
        ctx = make_context(guild, moderator, channel)
        cog = UnbanCog(SimpleNamespace())

        await cog.unban_user.callback(cog, ctx, "invalid")

        guild.fetch_ban.assert_not_awaited()
        guild.unban.assert_not_awaited()
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_reply_author_opens_ui(self) -> None:
        guild, moderator, target, channel = make_fixture()
        replied_message = SimpleNamespace(
            guild=guild,
            channel=channel,
            author=target,
            webhook_id=None,
        )
        reference = SimpleNamespace(
            resolved=None,
            cached_message=replied_message,
        )
        ctx = make_context(
            guild,
            moderator,
            channel,
            reference=reference,
        )
        cog = UnbanCog(SimpleNamespace())

        await cog.unban_user.callback(cog, ctx)

        self.assertEqual(guild.fetch_ban.await_args.args[0].id, target.id)
        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target_id, target.id)
        view.stop()

    async def test_reply_with_arguments_is_rejected_as_ambiguous(self) -> None:
        guild, moderator, target, channel = make_fixture()
        ctx = make_context(
            guild,
            moderator,
            channel,
            reference=SimpleNamespace(),
        )
        cog = UnbanCog(SimpleNamespace())

        await cog.unban_user.callback(cog, ctx, str(target.id))

        guild.fetch_ban.assert_not_awaited()
        self.assertIn("chỉ dùng lệnh", ctx.reply.await_args.args[0])
        self.assertNotIn("view", ctx.reply.await_args.kwargs)


class TestUnbanSubmission(unittest.IsolatedAsyncioTestCase):
    async def test_no_reinvite_fetches_unbans_and_records_exact_case(self) -> None:
        guild, moderator, target, channel = make_fixture()
        bot = SimpleNamespace()
        cog = UnbanCog(bot)
        interaction = make_interaction(guild, moderator, channel)
        request = UnbanRequest(target.id, False, "  appeal   accepted  ")

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
            return_value=17,
        ) as record_case:
            result = await cog._submit_unban(
                interaction,
                request,
                invite_channel=channel,
            )

        self.assertEqual(guild.fetch_ban.await_args.args[0].id, target.id)
        guild.unban.assert_awaited_once_with(
            target,
            reason="appeal accepted (Requested by moderator)",
        )
        record_case.assert_awaited_once_with(
            bot,
            guild=guild,
            target=target,
            moderator=moderator,
            action="unban",
            reason="appeal accepted",
        )
        channel.create_invite.assert_not_awaited()
        target.send.assert_not_awaited()
        self.assertTrue(result.completed)
        self.assertIn("Case #17", result.message)

    async def test_reinvite_has_exact_limits_and_is_dmed(self) -> None:
        guild, moderator, target, channel = make_fixture()
        cog = UnbanCog(SimpleNamespace())
        interaction = make_interaction(guild, moderator, channel)

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
            return_value=4,
        ):
            result = await cog._submit_unban(
                interaction,
                UnbanRequest(target.id, True, "Second chance"),
                invite_channel=channel,
            )

        channel.create_invite.assert_awaited_once()
        invite_kwargs = channel.create_invite.await_args.kwargs
        self.assertEqual(invite_kwargs["max_age"], REINVITE_MAX_AGE_SECONDS)
        self.assertEqual(invite_kwargs["max_age"], 604_800)
        self.assertEqual(invite_kwargs["max_uses"], 1)
        self.assertTrue(invite_kwargs["unique"])
        self.assertFalse(invite_kwargs["temporary"])
        target.send.assert_awaited_once()
        self.assertIn(
            "https://discord.gg/one-use",
            target.send.await_args.args[0],
        )
        self.assertTrue(result.completed)
        self.assertIsNone(result.private_message)
        self.assertNotIn("https://discord.gg/one-use", result.message)

    async def test_dm_forbidden_returns_private_invite_only(self) -> None:
        guild, moderator, target, channel = make_fixture()
        target.send.side_effect = make_forbidden_exception()
        cog = UnbanCog(SimpleNamespace())
        interaction = make_interaction(guild, moderator, channel)

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
            return_value=5,
        ):
            result = await cog._submit_unban(
                interaction,
                UnbanRequest(target.id, True, "Appeal"),
                invite_channel=channel,
            )

        self.assertTrue(result.completed)
        self.assertIsNone(result.private_message)
        self.assertNotIn("https://discord.gg/one-use", result.message)
        interaction.followup.send.assert_awaited_once()
        private_args = interaction.followup.send.await_args.args
        private_kwargs = interaction.followup.send.await_args.kwargs
        self.assertIn("https://discord.gg/one-use", private_args[0])
        self.assertTrue(private_kwargs["ephemeral"])

    async def test_undeliverable_private_invite_is_revoked(self) -> None:
        guild, moderator, target, channel = make_fixture()
        target.send.side_effect = make_forbidden_exception()
        moderator.send.side_effect = make_forbidden_exception()
        interaction = make_interaction(guild, moderator, channel)
        interaction.followup.send.side_effect = make_forbidden_exception()
        cog = UnbanCog(SimpleNamespace())

        with self.assertLogs("cogs.mod.unban", level="WARNING"):
            with patch(
                "cogs.mod.unban.record_case",
                new_callable=AsyncMock,
                return_value=8,
            ):
                result = await cog._submit_unban(
                    interaction,
                    UnbanRequest(target.id, True, "Appeal"),
                    invite_channel=channel,
                )

        invite = channel.create_invite.return_value
        invite.delete.assert_awaited_once()
        self.assertTrue(result.completed)
        self.assertIn("invite chưa dùng đã được hủy", result.message)
        self.assertNotIn(invite.url, result.message)

    async def test_invite_failure_is_terminal_after_unban_and_case(self) -> None:
        guild, moderator, target, channel = make_fixture()
        channel.create_invite.side_effect = make_forbidden_exception()
        cog = UnbanCog(SimpleNamespace())

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
            return_value=6,
        ) as record_case:
            result = await cog._submit_unban(
                make_interaction(guild, moderator, channel),
                UnbanRequest(target.id, True, "Appeal"),
                invite_channel=channel,
            )

        self.assertTrue(result.completed)
        guild.unban.assert_awaited_once()
        record_case.assert_awaited_once()
        channel.create_invite.assert_awaited_once()
        target.send.assert_not_awaited()
        self.assertIn("Đã unban nhưng", result.message)

    async def test_stale_fetch_not_found_is_terminal_without_mutation(self) -> None:
        guild, moderator, target, channel = make_fixture()
        guild.fetch_ban.side_effect = make_not_found_exception()
        cog = UnbanCog(SimpleNamespace())

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
        ) as record_case:
            result = await cog._submit_unban(
                make_interaction(guild, moderator, channel),
                UnbanRequest(target.id, False, "Appeal"),
                invite_channel=channel,
            )

        self.assertTrue(result.completed)
        guild.unban.assert_not_awaited()
        record_case.assert_not_awaited()
        channel.create_invite.assert_not_awaited()

    async def test_missing_create_invite_stops_before_unban(self) -> None:
        guild, moderator, target, channel = make_fixture()
        guild.me.can_invite = False
        cog = UnbanCog(SimpleNamespace())

        with patch(
            "cogs.mod.unban.record_case",
            new_callable=AsyncMock,
        ) as record_case:
            result = await cog._submit_unban(
                make_interaction(guild, moderator, channel),
                UnbanRequest(target.id, True, "Appeal"),
                invite_channel=channel,
            )

        self.assertFalse(result.completed)
        self.assertIn("Create Invite", result.message)
        guild.unban.assert_not_awaited()
        record_case.assert_not_awaited()
        channel.create_invite.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
