import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from cogs.mod._interaction_ui import FormAnswer
from cogs.mod.role import (
    ROLE_ASSIGN_REASON_CONFIG,
    ROLE_COPY_REASON_CONFIG,
    ROLE_REMOVE_REASON_CONFIG,
    ROLE_ROLL_SELECT_CUSTOM_ID,
    ROLE_UNROLL_SELECT_CUSTOM_ID,
    RoleCopyRequest,
    RoleCopyWorkflowView,
    RoleRollView,
    RoleUnrollView,
    RollCog,
    format_role_copy_result,
    plan_role_copy,
    role_assignment_denial,
    role_removal_denial,
    role_target_denial,
)


def embed_text(embed: discord.Embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.extend((field.name, field.value))
    return "\n".join(parts)


def assert_mentions_disabled(
    testcase: unittest.TestCase,
    allowed_mentions: discord.AllowedMentions,
) -> None:
    testcase.assertIsInstance(allowed_mentions, discord.AllowedMentions)
    testcase.assertFalse(allowed_mentions.everyone)
    testcase.assertFalse(allowed_mentions.users)
    testcase.assertFalse(allowed_mentions.roles)
    testcase.assertFalse(allowed_mentions.replied_user)


class FakeRole:
    def __init__(
        self,
        guild,
        role_id: int,
        position: int,
        *,
        name: str | None = None,
        default: bool = False,
        managed: bool = False,
    ) -> None:
        self.guild = guild
        self.id = role_id
        self.position = position
        self.name = name or f"role-{role_id}"
        self.mention = f"<@&{role_id}>"
        self.managed = managed
        self._default = default

    def is_default(self) -> bool:
        return self._default

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
        top_role: FakeRole,
        *,
        name: str,
        roles: list[FakeRole] | None = None,
        manage_roles: bool = True,
        bot: bool = False,
    ) -> None:
        self.guild = guild
        self.id = member_id
        self.top_role = top_role
        self.name = name
        self.roles = list(roles or [])
        self.guild_permissions = SimpleNamespace(manage_roles=manage_roles)
        self.mention = f"<@{member_id}>"
        self.bot = bot
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()

    def __str__(self) -> str:
        return self.name


class FakeGuild:
    def __init__(self) -> None:
        self.id = 10
        self.owner_id = 1_000
        self.me: FakeMember | None = None
        self.roles: list[FakeRole] = []
        self.members: dict[int, FakeMember] = {}
        self.fetch_member = AsyncMock()

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return next((role for role in self.roles if role.id == role_id), None)


class FakeChannel:
    def __init__(self) -> None:
        self.id = 555
        self.fetch_message = AsyncMock()


def make_fixture():
    guild = FakeGuild()
    everyone = FakeRole(guild, guild.id, 0, name="@everyone", default=True)
    bot_top = FakeRole(guild, 900, 100, name="Bot")
    moderator_top = FakeRole(guild, 800, 80, name="Moderator")
    source_top = FakeRole(guild, 600, 60, name="Source top")
    target_top = FakeRole(guild, 700, 10, name="Target top")
    eligible = FakeRole(guild, 200, 20, name="Raider")
    second = FakeRole(guild, 201, 25, name="Veteran")
    existing = FakeRole(guild, 202, 15, name="Existing")
    managed = FakeRole(guild, 203, 5, name="Integration", managed=True)
    guild.roles = [
        everyone,
        managed,
        target_top,
        existing,
        eligible,
        second,
        source_top,
        moderator_top,
        bot_top,
    ]
    bot_member = FakeMember(
        guild,
        999,
        bot_top,
        name="role-bot",
        roles=[everyone, bot_top],
        bot=True,
    )
    moderator = FakeMember(
        guild,
        42,
        moderator_top,
        name="moderator",
        roles=[everyone, moderator_top],
    )
    source = FakeMember(
        guild,
        66,
        source_top,
        name="source",
        roles=[everyone, eligible, second, existing, managed, source_top],
    )
    target = FakeMember(
        guild,
        77,
        target_top,
        name="target",
        roles=[everyone, target_top, existing],
    )
    guild.me = bot_member
    guild.members = {
        bot_member.id: bot_member,
        moderator.id: moderator,
        source.id: source,
        target.id: target,
    }
    guild.fetch_member.return_value = target
    return guild, moderator, source, target, eligible, second, managed


def make_context(
    guild: FakeGuild,
    moderator: FakeMember,
    *,
    reference=None,
):
    channel = FakeChannel()
    return SimpleNamespace(
        guild=guild,
        author=moderator,
        channel=channel,
        message=SimpleNamespace(reference=reference),
        clean_prefix="!tf ",
        reply=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


def make_reply_reference(ctx, target: FakeMember):
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


def make_interaction(guild: FakeGuild, moderator: FakeMember):
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


class TestRoleSafetyHelpers(unittest.TestCase):
    def test_assignment_and_removal_validate_current_membership(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()

        self.assertIsNone(
            role_assignment_denial(guild, moderator, target, eligible)
        )
        target.roles.append(eligible)
        self.assertIn(
            "đã có role",
            role_assignment_denial(guild, moderator, target, eligible),
        )
        self.assertIsNone(role_removal_denial(guild, moderator, target, eligible))
        target.roles.remove(eligible)
        self.assertIn(
            "không có role",
            role_removal_denial(guild, moderator, target, eligible),
        )

    def test_default_managed_and_higher_roles_are_denied(self) -> None:
        guild, moderator, _, target, _, _, managed = make_fixture()
        everyone = guild.roles[0]
        above_moderator = FakeRole(guild, 300, 80, name="Too high")

        self.assertIn(
            "@everyone",
            role_assignment_denial(guild, moderator, target, everyone),
        )
        self.assertIn(
            "integration",
            role_assignment_denial(guild, moderator, target, managed),
        )
        self.assertIn(
            "role cao nhất của mình",
            role_assignment_denial(guild, moderator, target, above_moderator),
        )

    def test_target_hierarchy_is_checked_for_moderator_and_bot(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        target.top_role.position = moderator.top_role.position
        self.assertIn("ngang hoặc cao hơn", role_target_denial(guild, moderator, target))

        target.top_role.position = guild.me.top_role.position
        guild.owner_id = moderator.id
        self.assertIn("bot phải cao hơn", role_target_denial(guild, moderator, target))

    def test_plan_preserves_only_safe_missing_roles_in_source_order(self) -> None:
        guild, moderator, source, target, eligible, second, managed = make_fixture()
        existing = guild.get_role(202)

        plan = plan_role_copy(guild, moderator, source, target)

        self.assertEqual(plan.eligible, (eligible, second, source.top_role))
        self.assertEqual(plan.already_present, (existing,))
        self.assertIn(managed, plan.unmanageable)
        self.assertIn(guild.roles[0], plan.unmanageable)

    def test_guild_owner_bypasses_only_moderator_role_hierarchy(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        high = FakeRole(guild, 300, 90, name="High")
        guild.roles.append(high)
        source.roles = [high]

        self.assertEqual(
            plan_role_copy(guild, moderator, source, target).unmanageable,
            (high,),
        )
        guild.owner_id = moderator.id
        self.assertEqual(
            plan_role_copy(guild, moderator, source, target).eligible,
            (high,),
        )


class TestRoleCommandConfiguration(unittest.TestCase):
    def test_rolecopy_cooldown_and_concurrency_remain_scoped(self) -> None:
        command = RollCog.copy_roles

        self.assertTrue(command.cooldown_after_parsing)
        self.assertEqual(command._buckets._type, commands.BucketType.user)
        self.assertIsNotNone(command._max_concurrency)
        self.assertEqual(command._max_concurrency.number, 1)
        self.assertEqual(command._max_concurrency.per, commands.BucketType.guild)
        self.assertFalse(command._max_concurrency.wait)

    def test_reply_capable_parameters_are_optional(self) -> None:
        self.assertFalse(RollCog.give_role.clean_params["member"].required)
        self.assertFalse(RollCog.remove_role.clean_params["member"].required)
        self.assertFalse(RollCog.copy_roles.clean_params["source"].required)
        self.assertFalse(RollCog.copy_roles.clean_params["target"].required)

    def test_role_reason_presets_are_operational_not_punitive(self) -> None:
        punishment_phrases = (
            "vi phạm nội quy",
            "spam",
            "quấy rối",
            "nội dung không phù hợp",
        )
        configs = (
            ROLE_ASSIGN_REASON_CONFIG,
            ROLE_REMOVE_REASON_CONFIG,
            ROLE_COPY_REASON_CONFIG,
        )
        for config in configs:
            rendered = " ".join(
                f"{preset.label} {preset.reason} {preset.description or ''}"
                for preset in config.presets
            ).lower()
            for phrase in punishment_phrases:
                with self.subTest(config=config.select_placeholder, phrase=phrase):
                    self.assertNotIn(phrase, rendered)
        self.assertTrue(
            any("yêu cầu" in preset.label.lower() for preset in ROLE_ASSIGN_REASON_CONFIG.presets)
        )
        self.assertTrue(
            any("gán nhầm" in preset.label.lower() for preset in ROLE_REMOVE_REASON_CONFIG.presets)
        )
        self.assertTrue(
            any("đồng bộ" in preset.label.lower() for preset in ROLE_COPY_REASON_CONFIG.presets)
        )


class TestRoleChangeWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_assignment_waits_for_reason_and_yes(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        self.assertEqual(view.role_select.custom_id, ROLE_ROLL_SELECT_CUSTOM_ID)
        await view.assign_role(interaction, eligible)

        target.add_roles.assert_not_awaited()
        self.assertEqual(view.step, "reason")
        await view.accept_reason(interaction, "Gán role theo yêu cầu của thành viên")
        target.add_roles.assert_not_awaited()
        await view.confirm(interaction)

        target.add_roles.assert_awaited_once()
        self.assertIn(
            "Gán role theo yêu cầu của thành viên",
            target.add_roles.await_args.kwargs["reason"],
        )
        self.assertTrue(view.completed)

    async def test_removal_waits_for_confirmation(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        target.roles.append(eligible)
        view = RoleUnrollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        self.assertEqual(view.role_select.custom_id, ROLE_UNROLL_SELECT_CUSTOM_ID)
        await view.remove_role(interaction, eligible)
        await view.accept_reason(interaction, "Role cleanup")

        target.remove_roles.assert_not_awaited()
        await view.confirm(interaction)
        target.remove_roles.assert_awaited_once()
        self.assertTrue(view.completed)

    async def test_hierarchy_is_rechecked_at_confirmation(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)
        await view.assign_role(interaction, eligible)
        await view.accept_reason(interaction, "Test")

        target.top_role.position = moderator.top_role.position
        await view.confirm(interaction)

        target.add_roles.assert_not_awaited()
        self.assertFalse(view.completed)
        self.assertIn(
            "ngang hoặc cao hơn",
            interaction.followup.send.await_args.args[0],
        )

    async def test_invalid_selection_does_not_advance_or_mutate(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        target.roles.append(eligible)
        view = RoleRollView(author_id=moderator.id, target=target)
        interaction = make_interaction(guild, moderator)

        await view.assign_role(interaction, eligible)

        self.assertEqual(view.step, "field:role_id")
        target.add_roles.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()


class TestRoleCommands(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_role_name_assigns_without_a_view(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        eligible.name = "Community Member"
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.give_role.callback(
            cog, ctx, target, role_name="community member",
        )

        target.add_roles.assert_awaited_once()
        self.assertIs(target.add_roles.await_args.args[0], eligible)
        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        assert_mentions_disabled(self, ctx.reply.await_args.kwargs["allowed_mentions"])

    async def test_explicit_role_mention_removes_without_a_view(self) -> None:
        guild, moderator, _, target, eligible, _, _ = make_fixture()
        target.roles.append(eligible)
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.remove_role.callback(
            cog, ctx, target, role_name=eligible.mention,
        )

        target.remove_roles.assert_awaited_once()
        self.assertIs(target.remove_roles.await_args.args[0], eligible)
        self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_unknown_or_unmanageable_direct_role_does_not_mutate(self) -> None:
        for role_name in ("missing role", "@everyone"):
            with self.subTest(role_name=role_name):
                guild, moderator, _, target, _, _, _ = make_fixture()
                cog = RollCog(SimpleNamespace())
                ctx = make_context(guild, moderator)

                await cog.give_role.callback(cog, ctx, target, role_name=role_name)

                target.add_roles.assert_not_awaited()
                self.assertNotIn("view", ctx.reply.await_args.kwargs)

    async def test_explicit_role_id_or_mention_takes_precedence_over_role_names(self) -> None:
        for use_mention in (False, True):
            with self.subTest(use_mention=use_mention):
                guild, moderator, _, target, eligible, _, _ = make_fixture()
                value = eligible.mention if use_mention else str(eligible.id)
                decoy = FakeRole(guild, 204, 5, name=value)
                guild.roles.insert(0, decoy)
                cog = RollCog(SimpleNamespace())
                ctx = make_context(guild, moderator)

                await cog.give_role.callback(cog, ctx, target, role_name=value)

                target.add_roles.assert_awaited_once()
                self.assertIs(target.add_roles.await_args.args[0], eligible)

    async def test_direct_and_reply_commands_open_views_without_mutation(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())

        direct_ctx = make_context(guild, moderator)
        await cog.give_role.callback(cog, direct_ctx, target)
        direct_view = direct_ctx.reply.await_args.kwargs["view"]
        self.assertIsInstance(direct_view, RoleRollView)

        reply_ctx = make_context(guild, moderator)
        reply_ctx.message.reference = make_reply_reference(reply_ctx, target)
        await cog.remove_role.callback(cog, reply_ctx)
        reply_view = reply_ctx.reply.await_args.kwargs["view"]
        self.assertIsInstance(reply_view, RoleUnrollView)
        self.assertEqual(reply_view.target.id, target.id)

        target.add_roles.assert_not_awaited()
        target.remove_roles.assert_not_awaited()
        direct_view.stop()
        reply_view.stop()

    async def test_reply_with_member_argument_is_rejected(self) -> None:
        guild, moderator, _, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.give_role.callback(cog, ctx, target)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("không kèm member", ctx.reply.await_args.args[0])

    async def test_missing_argument_error_still_has_safe_usage(self) -> None:
        guild, moderator, _, _, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        await cog.give_role_error(
            ctx,
            commands.MissingRequiredArgument(RollCog.give_role.params["member"]),
        )

        self.assertIn("roleroll @user", ctx.reply.await_args.args[0])
        self.assertIn("reply", ctx.reply.await_args.args[0])


class TestRoleCopyWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_complete_direct_command_copies_without_a_view(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.copy_roles.callback(cog, ctx, source, target)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertEqual(
            [call.args[0].id for call in target.add_roles.await_args_list],
            [eligible.id, second.id, source.top_role.id],
        )
        self.assertIn("Role đã sao chép", ctx.reply.await_args.args[0])

    async def test_reply_confirmation_attributes_source_and_lists_frozen_roles(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        ctx.message.reference = make_reply_reference(ctx, target)
        await cog.copy_roles.callback(cog, ctx)

        reply_kwargs = ctx.reply.await_args.kwargs
        view = reply_kwargs["view"]
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction, "source_id", FormAnswer(source.id, str(source)),
        )
        await view.accept_reason(interaction, "Approved copy")
        self.assertEqual(view.step, "confirm")
        confirmation_kwargs = interaction.response.edit_message.await_args.kwargs
        rendered = embed_text(confirmation_kwargs["embed"])
        self.assertIn("Nguồn", rendered)
        self.assertIn(str(source), rendered)
        self.assertIn(str(source.id), rendered)
        for role in (eligible, second, source.top_role):
            with self.subTest(role=role.name):
                self.assertIn(role.name, rendered)
                self.assertIn(str(role.id), rendered)
        self.assertFalse(reply_kwargs["mention_author"])
        assert_mentions_disabled(self, reply_kwargs["allowed_mentions"])
        assert_mentions_disabled(
            self,
            confirmation_kwargs["allowed_mentions"],
        )

    async def test_reply_command_freezes_preview_and_waits_for_yes(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        ctx.message.reference = make_reply_reference(ctx, target)
        await cog.copy_roles.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction, "source_id", FormAnswer(source.id, str(source)),
        )
        self.assertIsInstance(view, RoleCopyWorkflowView)
        self.assertEqual(view.step, "reason")
        self.assertEqual(
            tuple(role_id for role_id, _ in view._frozen_roles),
            (eligible.id, second.id, source.top_role.id),
        )
        target.add_roles.assert_not_awaited()

        # A role that appears after preview must never silently join the action.
        late = FakeRole(guild, 250, 30, name="Late")
        guild.roles.append(late)
        source.roles.append(late)
        interaction = make_interaction(guild, moderator)
        await view.accept_reason(interaction, "Approved copy")
        await view.confirm(interaction)

        copied_ids = [call.args[0].id for call in target.add_roles.await_args_list]
        self.assertEqual(
            copied_ids,
            [eligible.id, second.id, source.top_role.id],
        )
        self.assertNotIn(late.id, copied_ids)
        self.assertTrue(view.completed)

    async def test_reply_treats_author_as_destination_and_selects_source(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)

        await cog.copy_roles.callback(cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.assertEqual(view.target.id, target.id)
        self.assertEqual(view.step, "field:source_id")
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction,
            "source_id",
            FormAnswer(source.id, str(source)),
        )

        self.assertEqual(view._frozen_source_id, source.id)
        self.assertEqual(view.step, "reason")
        target.add_roles.assert_not_awaited()

        await view.accept_reason(interaction, "Approved copy")
        self.assertEqual(view.step, "confirm")
        rendered = embed_text(
            interaction.response.edit_message.await_args.kwargs["embed"]
        )
        self.assertIn("Nguồn", rendered)
        self.assertIn(str(source), rendered)
        self.assertIn(str(source.id), rendered)
        for role_id, role_display in view._frozen_roles:
            with self.subTest(role_id=role_id):
                self.assertIn(role_display.split(" (`", 1)[0], rendered)
                self.assertIn(str(role_id), rendered)
        assert_mentions_disabled(
            self,
            interaction.response.edit_message.await_args.kwargs[
                "allowed_mentions"
            ],
        )

    async def test_confirmation_uses_frozen_roles_and_rechecks_live_state(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)
        ctx.message.reference = make_reply_reference(ctx, target)
        await cog.copy_roles.callback(cog, ctx)
        view = ctx.reply.await_args.kwargs["view"]
        interaction = make_interaction(guild, moderator)
        await view.accept_answer(
            interaction, "source_id", FormAnswer(source.id, str(source)),
        )

        source.roles.remove(eligible)
        second.position = guild.me.top_role.position
        interaction = make_interaction(guild, moderator)
        await view.accept_reason(interaction, "Copy")
        await view.confirm(interaction)

        target.add_roles.assert_awaited_once()
        self.assertIs(target.add_roles.await_args.args[0], source.top_role)
        result = interaction.edit_original_response.await_args.kwargs["content"]
        self.assertIn("nguồn đã thay đổi", result)
        self.assertIn("không thể quản lý", result)

    async def test_partial_failure_is_terminal_and_reports_remaining_roles(self) -> None:
        guild, moderator, source, target, eligible, second, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        target.add_roles.side_effect = [None, make_forbidden_exception()]
        interaction = make_interaction(guild, moderator)
        request = RoleCopyRequest(
            source_id=source.id,
            target_id=target.id,
            role_ids=(eligible.id, second.id),
            reason="Copy",
        )

        with self.assertLogs("cogs.mod.role", level="WARNING"):
            result = await cog._submit_role_copy(interaction, request)

        self.assertTrue(result.completed)
        self.assertIn("Đã sao chép **1**", result.message)
        self.assertIn("Lỗi: 1 role", result.message)

    async def test_completed_response_lists_only_roles_actually_copied(self) -> None:
        guild, moderator, source, target, eligible, second, managed = make_fixture()
        existing = guild.get_role(202)
        cog = RollCog(SimpleNamespace())
        view = RoleCopyWorkflowView(
            author_id=moderator.id,
            target=target,
            source=source,
            plan=plan_role_copy(guild, moderator, source, target),
            submitter=cog._submit_role_copy,
        )
        # Exercise every outcome without relying on the initial plan filtering:
        # copied, already present, unmanageable, failed, and not attempted.
        view._frozen_roles = tuple(
            (role.id, f"{role.name} (`{role.id}`)")
            for role in (eligible, existing, managed, second, source.top_role)
        )
        target.add_roles.side_effect = [None, make_forbidden_exception()]
        interaction = make_interaction(guild, moderator)
        await view.accept_reason(interaction, "Approved copy")

        with self.assertLogs("cogs.mod.role", level="WARNING"):
            await view.confirm(interaction)

        result_kwargs = interaction.edit_original_response.await_args.kwargs
        content = result_kwargs["content"]
        self.assertLessEqual(len(content), 2000)
        self.assertIn(source.mention, content)
        self.assertIn(target.mention, content)
        self.assertIn("Role đã sao chép", content)
        self.assertIn(eligible.name, content)
        self.assertIn(str(eligible.id), content)
        for role in (existing, managed, second, source.top_role):
            with self.subTest(not_copied=role.name):
                self.assertNotIn(role.name, content)
                self.assertNotIn(str(role.id), content)
        self.assertIn("Bỏ qua", content)
        self.assertIn("Lỗi: 1 role", content)
        self.assertIn("Chưa thử: 1 role", content)
        assert_mentions_disabled(self, result_kwargs["allowed_mentions"])

        audit_reason = target.add_roles.await_args_list[0].kwargs["reason"]
        self.assertIn(f"source={source.id}", audit_reason)
        self.assertIn(f"target={target.id}", audit_reason)
        self.assertLessEqual(len(audit_reason), 512)

    async def test_long_reason_keeps_source_attribution_in_audit_log(self) -> None:
        guild, moderator, source, target, eligible, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        request = RoleCopyRequest(
            source_id=source.id,
            target_id=target.id,
            role_ids=(eligible.id,),
            reason="x" * 1000,
        )

        result = await cog._submit_role_copy(
            make_interaction(guild, moderator),
            request,
        )

        self.assertTrue(result.completed)
        audit_reason = target.add_roles.await_args.kwargs["reason"]
        self.assertTrue(audit_reason.startswith(f"rolecopy source={source.id}"))
        self.assertIn(f"target={target.id}", audit_reason)
        self.assertLessEqual(len(audit_reason), 512)

    def test_completed_role_table_is_bounded_for_discord_message(self) -> None:
        guild, _, source, target, _, _, _ = make_fixture()
        copied = [
            FakeRole(
                guild,
                10_000 + index,
                20,
                name=f"copied-{index}-" + ("x" * 80),
            )
            for index in range(100)
        ]

        content = format_role_copy_result(
            source,
            target,
            copied=copied,
            failed=[],
            not_attempted=[],
        )

        self.assertLessEqual(len(content), 2000)
        self.assertIn("Role đã sao chép", content)
        self.assertIn("copied-0-", content)
        self.assertIn(str(copied[0].id), content)
        self.assertIn(source.mention, content)
        self.assertIn(target.mention, content)
        self.assertIn("role khác", content)
        self.assertEqual(content.count("```"), 2)

    async def test_execution_lock_rejects_a_second_confirmation(self) -> None:
        guild, moderator, source, target, eligible, _, _ = make_fixture()
        cog = RollCog(SimpleNamespace())
        lock = cog._role_copy_locks.setdefault(guild.id, asyncio.Lock())
        await lock.acquire()
        try:
            result = await cog._submit_role_copy(
                make_interaction(guild, moderator),
                RoleCopyRequest(
                    source_id=source.id,
                    target_id=target.id,
                    role_ids=(eligible.id,),
                    reason="Copy",
                ),
            )
        finally:
            lock.release()

        self.assertFalse(result.completed)
        self.assertIn("đang được xử lý", result.message)
        target.add_roles.assert_not_awaited()

    async def test_no_eligible_direct_copy_returns_summary_without_view(self) -> None:
        guild, moderator, source, target, _, _, _ = make_fixture()
        source.roles = [guild.roles[0], guild.get_role(202)]
        cog = RollCog(SimpleNamespace())
        ctx = make_context(guild, moderator)

        await cog.copy_roles.callback(cog, ctx, source, target)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("Không có role mới", ctx.reply.await_args.args[0])
        target.add_roles.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
