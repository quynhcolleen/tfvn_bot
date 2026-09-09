import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.mod._interaction_ui import FormAnswer
from cogs.mod.cases import ModerationCasesCog


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.guild_permissions = SimpleNamespace(
            manage_messages=True,
            manage_guild=True,
        )


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10


def make_context(guild, moderator):
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        send=AsyncMock(),
    )


def make_interaction(guild, moderator):
    return SimpleNamespace(
        guild=guild,
        user=moderator,
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TestCaseMutationGuards(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        cog = object.__new__(ModerationCasesCog)
        cog.bot = SimpleNamespace()
        cog.cases = MagicMock()
        cog.config = MagicMock()
        cog._send_case_log = AsyncMock()
        return cog

    async def test_edit_uses_optimistic_value_after_confirmation(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        ctx = make_context(guild, moderator)
        cog = self.make_cog()
        updated_at = object()
        cog.cases.find_one.return_value = {
            "_id": "case-id",
            "guild_id": guild.id,
            "case_number": 7,
            "reason": "old reason",
            "updated_at": updated_at,
        }
        cog.cases.find_one_and_update.return_value = {
            "case_number": 7,
            "reason": "new reason",
        }

        await cog.case_edit.callback(cog, ctx, 7)

        cog.cases.find_one_and_update.assert_not_called()
        view = ctx.reply.await_args.kwargs["view"]
        view.values["new_reason"] = FormAnswer("new reason", "new reason")
        view._show_confirm_step()
        await view.confirm(make_interaction(guild, moderator))

        query = cog.cases.find_one_and_update.call_args.args[0]
        self.assertEqual(query["reason"], "old reason")
        self.assertIs(query["updated_at"], updated_at)
        cog._send_case_log.assert_awaited_once()

    async def test_status_cancel_does_not_update_case(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        ctx = make_context(guild, moderator)
        cog = self.make_cog()
        cog.cases.find_one.return_value = {
            "_id": "case-id",
            "guild_id": guild.id,
            "case_number": 8,
            "status": "open",
            "updated_at": object(),
        }

        await cog.case_status.callback(cog, ctx, 8)
        view = ctx.reply.await_args.kwargs["view"]
        await view.cancel(make_interaction(guild, moderator))

        cog.cases.find_one_and_update.assert_not_called()
        self.assertTrue(view.is_finished())

    async def test_edit_noop_still_rejects_stale_panel(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        ctx = make_context(guild, moderator)
        cog = self.make_cog()
        updated_at = object()
        cog.cases.find_one.side_effect = [
            {
                "_id": "case-id",
                "guild_id": guild.id,
                "case_number": 9,
                "reason": "same reason",
                "updated_at": updated_at,
            },
            None,
        ]

        await cog.case_edit.callback(cog, ctx, 9)
        view = ctx.reply.await_args.kwargs["view"]
        view.values["new_reason"] = FormAnswer("same reason", "same reason")
        view._show_confirm_step()
        interaction = make_interaction(guild, moderator)
        await view.confirm(interaction)

        cog.cases.find_one_and_update.assert_not_called()
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("đã thay đổi", content)

    async def test_status_noop_still_rejects_stale_panel(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        ctx = make_context(guild, moderator)
        cog = self.make_cog()
        updated_at = object()
        cog.cases.find_one.side_effect = [
            {
                "_id": "case-id",
                "guild_id": guild.id,
                "case_number": 10,
                "status": "open",
                "updated_at": updated_at,
            },
            None,
        ]

        await cog.case_status.callback(cog, ctx, 10)
        view = ctx.reply.await_args.kwargs["view"]
        view.values["status"] = FormAnswer("open", "Open")
        view._show_confirm_step()
        interaction = make_interaction(guild, moderator)
        await view.confirm(interaction)

        cog.cases.find_one_and_update.assert_not_called()
        content = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("đã thay đổi", content)


if __name__ == "__main__":
    unittest.main()
