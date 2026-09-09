import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.mod._interaction_ui import ConfigurableModerationView, FormAnswer
from cogs.mod.nickname import (
    MAX_NICKNAME_LENGTH,
    NicknameCog,
    nickname_change_denial,
    normalize_nickname,
)


class FakeRole:
    def __init__(self, position: int) -> None:
        self.position = position

    def __lt__(self, other) -> bool:
        return self.position < other.position

    def __le__(self, other) -> bool:
        return self.position <= other.position

    def __gt__(self, other) -> bool:
        return self.position > other.position

    def __ge__(self, other) -> bool:
        return self.position >= other.position


class FakeMember:
    def __init__(
        self,
        guild,
        member_id: int,
        position: int,
        *,
        name: str,
        manage_nicknames: bool = True,
        bot: bool = False,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.top_role = FakeRole(position)
        self.name = name
        self.nick: str | None = None
        self.bot = bot
        self.guild_permissions = SimpleNamespace(
            manage_nicknames=manage_nicknames,
        )
        self.mention = f"<@{member_id}>"
        self.edit = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1_000
        self.me: FakeMember | None = None
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)


class FakeChannel:
    def __init__(self) -> None:
        self.id = 555
        self.fetch_message = AsyncMock()


def make_fixture():
    guild = FakeGuild()
    bot_member = FakeMember(guild, 999, 100, name="bot", bot=True)
    moderator = FakeMember(guild, 42, 80, name="moderator")
    target = FakeMember(guild, 77, 10, name="target")
    guild.me = bot_member
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        target.id: target,
    }
    guild.fetch_member.return_value = target
    return guild, moderator, target


def make_context(guild, moderator, *, reference=None):
    channel = FakeChannel()
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=SimpleNamespace(reference=reference),
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


def make_reply_reference(ctx, target):
    message = SimpleNamespace(
        id=123,
        guild=ctx.guild,
        channel=ctx.channel,
        author=target,
        webhook_id=None,
    )
    return SimpleNamespace(
        message_id=message.id,
        channel_id=ctx.channel.id,
        resolved=None,
        cached_message=message,
    )


def make_interaction(guild, moderator):
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


class TestNicknameValidation(unittest.TestCase):
    def test_nickname_is_trimmed_and_limited_to_discord_limit(self) -> None:
        self.assertEqual(normalize_nickname("  New Name  "), "New Name")
        self.assertEqual(
            normalize_nickname("x" * MAX_NICKNAME_LENGTH),
            "x" * MAX_NICKNAME_LENGTH,
        )
        with self.assertRaisesRegex(ValueError, "32"):
            normalize_nickname("x" * (MAX_NICKNAME_LENGTH + 1))
        with self.assertRaisesRegex(ValueError, "để trống"):
            normalize_nickname("   ")

    def test_live_hierarchy_and_bot_permissions_are_enforced(self) -> None:
        guild, moderator, target = make_fixture()
        target.top_role.position = moderator.top_role.position
        self.assertIn(
            "ngang hoặc cao hơn",
            nickname_change_denial(guild, moderator, target),
        )

        target.top_role.position = 10
        guild.me.guild_permissions.manage_nicknames = False
        self.assertIn(
            "Bot không có quyền",
            nickname_change_denial(guild, moderator, target),
        )


class TestNicknameWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_complete_direct_syntax_edits_without_a_view(self) -> None:
        guild, moderator, target = make_fixture()
        cog = NicknameCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.change_nickname.callback(
            cog,
            ctx,
            target,
            new_nickname="  New Name  ",
        )

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        target.edit.assert_awaited_once()
        self.assertEqual(target.edit.await_args.kwargs["nick"], "New Name")

    async def test_invalid_direct_nickname_does_not_edit(self) -> None:
        for nickname in ("   ", "x" * (MAX_NICKNAME_LENGTH + 1)):
            with self.subTest(nickname=nickname):
                guild, moderator, target = make_fixture()
                cog = NicknameCog(SimpleNamespace())
                ctx = make_context(guild, moderator)

                await cog.change_nickname.callback(
                    cog, ctx, target, new_nickname=nickname,
                )

                self.assertNotIn("view", ctx.reply.await_args.kwargs)
                target.edit.assert_not_awaited()

    async def test_reply_without_arguments_targets_replied_member(self) -> None:
        guild, moderator, target = make_fixture()
        cog = NicknameCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.change_nickname.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target.id, target.id)
        self.assertNotIn("nickname", view.values)
        target.edit.assert_not_awaited()
        view.stop()

    async def test_reply_with_direct_arguments_is_rejected(self) -> None:
        guild, moderator, target = make_fixture()
        cog = NicknameCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.change_nickname.callback(
            cog,
            ctx,
            target,
            new_nickname="New Name",
        )

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("không kèm tham số", ctx.reply.await_args.args[0])
        target.edit.assert_not_awaited()

    async def test_edit_occurs_only_after_reason_and_yes(self) -> None:
        guild, moderator, target = make_fixture()
        cog = NicknameCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        await cog.change_nickname.callback(cog, ctx, target)
        view = ctx.reply.await_args.kwargs["view"]
        interaction = make_interaction(guild, moderator)

        await view.accept_answer(
            interaction,
            "nickname",
            FormAnswer("New Name", "New Name"),
        )
        target.edit.assert_not_awaited()
        await view.accept_reason(interaction, "Theo yêu cầu")
        target.edit.assert_not_awaited()
        await view.confirm(interaction)

        target.edit.assert_awaited_once()
        self.assertEqual(target.edit.await_args.kwargs["nick"], "New Name")
        self.assertIn("Theo yêu cầu", target.edit.await_args.kwargs["reason"])
        self.assertTrue(view.completed)

    async def test_hierarchy_change_before_yes_blocks_edit(self) -> None:
        guild, moderator, target = make_fixture()
        cog = NicknameCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        await cog.change_nickname.callback(cog, ctx, target)
        view = ctx.reply.await_args.kwargs["view"]
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction,
            "nickname",
            FormAnswer("New Name", "New Name"),
        )
        await view.accept_reason(interaction, "Theo yêu cầu")

        target.top_role.position = moderator.top_role.position
        await view.confirm(interaction)

        target.edit.assert_not_awaited()
        self.assertFalse(view.completed)
        self.assertIn(
            "ngang hoặc cao hơn",
            interaction.followup.send.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
