import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.mod._ban_ui import (
    BAN_UI_TIMEOUT_SECONDS,
    BanActionResult,
    BanRequest,
    BanWorkflowView,
    CustomHoursModal,
    CustomReasonModal,
    format_delete_message_window,
    parse_delete_message_hours,
)
from cogs.mod.ban import BanCog


class FakeRole:
    def __init__(self, position: int) -> None:
        self.position = position

    def __gt__(self, other: "FakeRole") -> bool:
        return self.position > other.position

    def __le__(self, other: "FakeRole") -> bool:
        return self.position <= other.position


class FakeMember:
    def __init__(
        self,
        guild: "FakeGuild",
        member_id: int,
        role_position: int,
        *,
        name: str,
        can_ban: bool = True,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.top_role = FakeRole(role_position)
        self.guild_permissions = SimpleNamespace(ban_members=can_ban)
        self.name = name
        self.ban = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self, guild_id: int = 10, owner_id: int = 1_000) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.me: FakeMember | None = None
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()
        self.ban = AsyncMock()

    def add_member(self, member: FakeMember) -> None:
        self.members[member.id] = member

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)


def make_fixture() -> tuple[FakeGuild, FakeMember, FakeMember]:
    guild = FakeGuild()
    bot_member = FakeMember(guild, 999, 100, name="ban-bot")
    moderator = FakeMember(guild, 42, 80, name="moderator")
    target = FakeMember(guild, 77, 10, name="target")
    guild.me = bot_member
    for member in (bot_member, moderator, target):
        guild.add_member(member)
    return guild, moderator, target


def make_interaction(
    guild: FakeGuild | None,
    user: FakeMember,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        user=user,
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
    *,
    reference: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=SimpleNamespace(id=555, fetch_message=AsyncMock()),
        clean_prefix="!tf ",
        message=SimpleNamespace(reference=reference),
        reply=AsyncMock(return_value=SimpleNamespace(id=123)),
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
        {"code": 10013, "message": "Unknown User"},
    )


class TestBanHourHelpers(unittest.TestCase):
    def test_parse_delete_hours_and_convert_to_seconds(self) -> None:
        self.assertEqual(parse_delete_message_hours(" 0 "), 0)
        self.assertEqual(parse_delete_message_hours("36"), 36)
        self.assertEqual(parse_delete_message_hours("168"), 168)

        for value in ("", " ", "-1", "1.5", "abc", "169"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_delete_message_hours(value)

        request = BanRequest(
            target_id=77,
            delete_message_hours=168,
            reason="Spam",
        )
        self.assertEqual(request.delete_message_seconds, 604_800)
        self.assertEqual(format_delete_message_window(0), "Không xóa tin nhắn cũ")
        self.assertEqual(format_delete_message_window(24), "24 giờ (1 ngày) gần nhất")


class TestBanWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_preset_steps_and_yes_confirmation(self) -> None:
        guild, moderator, target = make_fixture()
        submitter = AsyncMock(
            return_value=BanActionResult(True, "Ban completed")
        )
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=submitter,
        )
        self.assertEqual(view.timeout, BAN_UI_TIMEOUT_SECONDS)
        self.assertEqual(view.step, "delete")
        self.assertEqual(
            view.children,
            [
                view.delete_hours_select,
                view.custom_hours_button,
                view.cancel_button,
            ],
        )

        hours_interaction = make_interaction(guild, moderator)
        view.delete_hours_select._values = ["24"]
        await view.delete_hours_select.callback(hours_interaction)

        self.assertEqual(view.delete_message_hours, 24)
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
        hours_interaction.response.edit_message.assert_awaited_once()

        reason_interaction = make_interaction(guild, moderator)
        view.reason_select._values = ["spam"]
        await view.reason_select.callback(reason_interaction)

        self.assertEqual(view.reason, "Spam hoặc quảng cáo không được phép")
        self.assertEqual(view.step, "confirm")
        self.assertEqual(
            view.children,
            [view.confirm_button, view.cancel_button],
        )

        confirm_interaction = make_interaction(guild, moderator)
        await view.confirm_button.callback(confirm_interaction)

        submitter.assert_awaited_once_with(
            confirm_interaction,
            BanRequest(
                target_id=target.id,
                delete_message_hours=24,
                reason="Spam hoặc quảng cáo không được phép",
            ),
        )
        confirm_interaction.response.defer.assert_awaited_once_with()
        confirm_interaction.edit_original_response.assert_awaited_once()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_custom_hours_and_reason_modals_advance_workflow(self) -> None:
        guild, moderator, target = make_fixture()
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )

        open_hours = make_interaction(guild, moderator)
        await view.custom_hours_button.callback(open_hours)
        open_hours.response.send_modal.assert_awaited_once()
        hours_modal = open_hours.response.send_modal.await_args.args[0]
        self.assertIsInstance(hours_modal, CustomHoursModal)
        hours_modal.hours._value = "36"

        submit_hours = make_interaction(guild, moderator)
        await hours_modal.on_submit(submit_hours)
        self.assertEqual(view.delete_message_hours, 36)
        self.assertEqual(view.step, "reason")
        submit_hours.response.edit_message.assert_awaited_once()

        open_reason = make_interaction(guild, moderator)
        await view.custom_reason_button.callback(open_reason)
        open_reason.response.send_modal.assert_awaited_once()
        reason_modal = open_reason.response.send_modal.await_args.args[0]
        self.assertIsInstance(reason_modal, CustomReasonModal)
        reason_modal.reason._value = "  repeated    harassment  "

        submit_reason = make_interaction(guild, moderator)
        await reason_modal.on_submit(submit_reason)
        self.assertEqual(view.reason, "repeated harassment")
        self.assertEqual(view.step, "confirm")
        submit_reason.response.edit_message.assert_awaited_once()
        view.stop()

    async def test_no_cancels_without_submitting(self) -> None:
        guild, moderator, target = make_fixture()
        submitter = AsyncMock()
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=submitter,
        )
        interaction = make_interaction(guild, moderator)

        await view.cancel_button.callback(interaction)

        submitter.assert_not_awaited()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIn("Đã hủy ban", kwargs["content"])
        self.assertIsNone(kwargs["embed"])

    async def test_timeout_disables_current_controls(self) -> None:
        guild, moderator, target = make_fixture()
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        self.assertTrue(all(item.disabled for item in view.children))
        view.message.edit.assert_awaited_once_with(view=view)
        view.stop()

    async def test_owner_lock_and_live_permission_rechecks(self) -> None:
        guild, moderator, target = make_fixture()
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )

        valid = make_interaction(guild, moderator)
        self.assertTrue(await view.interaction_check(valid))

        stranger = FakeMember(guild, 43, 70, name="other-moderator")
        guild.add_member(stranger)
        stranger_interaction = make_interaction(guild, stranger)
        self.assertFalse(await view.interaction_check(stranger_interaction))
        stranger_interaction.response.send_message.assert_awaited_once_with(
            "Chỉ moderator đã mở bảng ban này mới có thể sử dụng.",
            ephemeral=True,
        )

        moderator.guild_permissions.ban_members = False
        lost_permission = make_interaction(guild, moderator)
        self.assertFalse(await view.interaction_check(lost_permission))
        lost_permission.response.send_message.assert_awaited_once_with(
            "Bạn không còn quyền Ban Members để thực hiện thao tác này.",
            ephemeral=True,
        )

        moderator.guild_permissions.ban_members = True
        guild.me.guild_permissions.ban_members = False
        bot_lost_permission = make_interaction(guild, moderator)
        self.assertFalse(await view.interaction_check(bot_lost_permission))
        bot_lost_permission.response.send_message.assert_awaited_once_with(
            "Bot không có quyền Ban Members trong server này.",
            ephemeral=True,
        )
        view.stop()


class TestBanCommandDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_member_bans_without_ui_and_keeps_legacy_deletion(self) -> None:
        guild, moderator, target = make_fixture()
        cog = BanCog(SimpleNamespace())
        cog._resolve_reply_target = AsyncMock()
        ctx = make_context(guild, moderator)

        with patch("cogs.mod.ban.record_case", new=AsyncMock(return_value=7)):
            await cog.ban_member.callback(
                cog,
                ctx,
                target,
                reason="  supplied   reason  ",
            )

        cog._resolve_reply_target.assert_not_awaited()
        ctx.reply.assert_awaited_once()
        kwargs = ctx.reply.await_args.kwargs
        self.assertNotIn("view", kwargs)
        target.ban.assert_awaited_once_with(
            reason="supplied reason (Requested by moderator)",
            delete_message_seconds=86400,
        )
        self.assertIn("Case #7", ctx.reply.await_args.args[0])
        self.assertFalse(kwargs["mention_author"])

    async def test_direct_ban_still_rejects_higher_role(self) -> None:
        guild, moderator, target = make_fixture()
        target.top_role.position = moderator.top_role.position
        cog = BanCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.ban_member.callback(cog, ctx, target)

        target.ban.assert_not_awaited()
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_explicit_member_without_reason_uses_legacy_default(self) -> None:
        guild, moderator, target = make_fixture()
        cog = BanCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        with patch("cogs.mod.ban.record_case", new=AsyncMock(return_value=None)):
            await cog.ban_member.callback(cog, ctx, target)

        target.ban.assert_awaited_once_with(
            reason="Không có lý do cụ thể (Requested by moderator)",
            delete_message_seconds=86400,
        )
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_reply_author_is_used_when_member_is_omitted(self) -> None:
        guild, moderator, target = make_fixture()
        referenced_message = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=555),
            author=target,
        )
        reference = SimpleNamespace(
            resolved=None,
            cached_message=referenced_message,
        )
        ctx = make_context(guild, moderator, reference=reference)
        cog = BanCog(SimpleNamespace())

        await cog.ban_member.callback(cog, ctx)

        guild.fetch_member.assert_not_awaited()
        ctx.reply.assert_awaited_once()
        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target_id, target.id)
        self.assertIs(view.message, ctx.reply.return_value)
        view.stop()

    async def test_reply_with_arguments_is_rejected_as_ambiguous(self) -> None:
        guild, moderator, target = make_fixture()
        ctx = make_context(
            guild,
            moderator,
            reference=SimpleNamespace(),
        )
        cog = BanCog(SimpleNamespace())
        cog._resolve_reply_target = AsyncMock()

        await cog.ban_member.callback(
            cog,
            ctx,
            target,
            reason="Spam",
        )

        cog._resolve_reply_target.assert_not_awaited()
        ctx.reply.assert_awaited_once()
        args = ctx.reply.await_args.args
        kwargs = ctx.reply.await_args.kwargs
        self.assertIn("chỉ dùng lệnh", args[0])
        self.assertNotIn("view", kwargs)

    async def test_departed_reply_author_can_still_open_ui(self) -> None:
        guild, moderator, _ = make_fixture()
        departed = FakeMember(guild, 88, 10, name="departed")
        referenced_message = SimpleNamespace(
            guild=guild,
            channel=SimpleNamespace(id=555),
            author=departed,
        )
        ctx = make_context(
            guild,
            moderator,
            reference=SimpleNamespace(
                resolved=None,
                cached_message=referenced_message,
            ),
        )
        guild.fetch_member.side_effect = make_not_found_exception()
        cog = BanCog(SimpleNamespace())

        await cog.ban_member.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target_id, departed.id)
        view.stop()


class TestBanSubmission(unittest.IsolatedAsyncioTestCase):
    async def test_submit_bans_with_exact_deletion_window_and_records_case(
        self,
    ) -> None:
        guild, moderator, target = make_fixture()
        bot = SimpleNamespace()
        cog = BanCog(bot)
        interaction = make_interaction(guild, moderator)
        request = BanRequest(
            target_id=target.id,
            delete_message_hours=36,
            reason="  repeated   spam  ",
        )

        with patch(
            "cogs.mod.ban.record_case",
            new_callable=AsyncMock,
            return_value=17,
        ) as record_case:
            result = await cog._submit_ban(interaction, request)

        target.ban.assert_awaited_once_with(
            reason="repeated spam (Requested by moderator)",
            delete_message_seconds=129_600,
        )
        record_case.assert_awaited_once_with(
            bot,
            guild=guild,
            target=target,
            moderator=moderator,
            action="ban",
            reason="repeated spam",
        )
        self.assertTrue(result.completed)
        self.assertIn("Case #17", result.message)
        self.assertIn("36 giờ gần nhất", result.message)

    async def test_discord_forbidden_is_retryable_and_does_not_record_case(
        self,
    ) -> None:
        guild, moderator, target = make_fixture()
        target.ban.side_effect = make_forbidden_exception()
        cog = BanCog(SimpleNamespace())
        interaction = make_interaction(guild, moderator)
        request = BanRequest(target.id, 1, "Spam")

        with patch(
            "cogs.mod.ban.record_case",
            new_callable=AsyncMock,
        ) as record_case:
            result = await cog._submit_ban(interaction, request)

        target.ban.assert_awaited_once_with(
            reason="Spam (Requested by moderator)",
            delete_message_seconds=3_600,
        )
        record_case.assert_not_awaited()
        self.assertFalse(result.completed)
        self.assertIn("không thể ban", result.message)

    async def test_departed_target_uses_guild_ban(self) -> None:
        guild, moderator, _ = make_fixture()
        departed = FakeMember(guild, 88, 10, name="departed")
        cog = BanCog(SimpleNamespace())
        interaction = make_interaction(guild, moderator)
        request = BanRequest(departed.id, 0, "Raid")

        with patch(
            "cogs.mod.ban.record_case",
            new_callable=AsyncMock,
            return_value=9,
        ):
            result = await cog._submit_ban(
                interaction,
                request,
                fallback_target=departed,
            )

        departed.ban.assert_not_awaited()
        guild.ban.assert_awaited_once_with(
            departed,
            reason="Raid (Requested by moderator)",
            delete_message_seconds=0,
        )
        self.assertTrue(result.completed)

    async def test_pathological_markdown_reason_stays_within_payload_limits(
        self,
    ) -> None:
        guild, moderator, target = make_fixture()
        reason = "*" * 1000
        view = BanWorkflowView(
            author_id=moderator.id,
            guild_id=guild.id,
            target=target,
            submitter=AsyncMock(),
        )
        view.delete_message_hours = 168
        view.reason = reason
        view._show_confirm_step()

        embed = view.build_embed()
        reason_field = next(field for field in embed.fields if field.name == "Lý do")
        self.assertLessEqual(len(reason_field.value), 1024)
        view.stop()

        cog = BanCog(SimpleNamespace())
        interaction = make_interaction(guild, moderator)
        with patch(
            "cogs.mod.ban.record_case",
            new_callable=AsyncMock,
            return_value=1,
        ):
            result = await cog._submit_ban(
                interaction,
                BanRequest(target.id, 168, reason),
            )

        self.assertTrue(result.completed)
        self.assertLessEqual(len(result.message), 2000)


if __name__ == "__main__":
    unittest.main()
