import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.mod._interaction_ui import (
    ActionResult,
    COMMON_REASON_CONFIG,
    ConfigurableModerationView,
    FormAnswer,
    IntegerField,
    ModalField,
    ModalInput,
    MODERATION_UI_TIMEOUT_SECONDS,
    ReasonConfig,
    ReasonPreset,
    WorkflowSpec,
    WorkflowTarget,
)


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.guild_permissions = SimpleNamespace(manage_messages=True)


class FakeGuild:
    def __init__(self, guild_id: int = 10) -> None:
        self.id = guild_id


def make_interaction(guild, user):
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


def make_view(*, submitter=None, permission_check=None):
    spec = WorkflowSpec(
        namespace="test-action",
        title="Test action",
        action_text="thực hiện test",
        confirm_label="Có, thực hiện",
        fields=(
            IntegerField(
                "count",
                "Số lượng",
                minimum=1,
                maximum=25,
            ),
        ),
        reason=COMMON_REASON_CONFIG,
    )
    return ConfigurableModerationView(
        spec=spec,
        author_id=42,
        guild_id=10,
        target=WorkflowTarget(77, "target"),
        submitter=submitter
        or AsyncMock(return_value=ActionResult(True, "completed")),
        request_builder=lambda answers, reason: (
            answers["count"].value,
            reason,
        ),
        live_permission_check=permission_check or (lambda _guild, _member: None),
    )


class TestConfigurableModerationView(unittest.IsolatedAsyncioTestCase):
    async def test_form_reason_and_confirmation_are_sequential(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        submitter = AsyncMock(return_value=ActionResult(True, "completed"))
        view = make_view(submitter=submitter)
        self.assertEqual(view.timeout, MODERATION_UI_TIMEOUT_SECONDS)
        self.assertEqual(view.step, "field:count")

        open_form = make_interaction(guild, moderator)
        await view.children[0].callback(open_form)
        modal = open_form.response.send_modal.await_args.args[0]
        modal.input._value = "12"

        submit_form = make_interaction(guild, moderator)
        await modal.on_submit(submit_form)
        self.assertEqual(view.values["count"], FormAnswer(12, "12"))
        self.assertEqual(view.step, "reason")

        reason_interaction = make_interaction(guild, moderator)
        reason_select = view.children[0]
        reason_select._values = ["spam"]
        await reason_select.callback(reason_interaction)
        self.assertEqual(view.step, "confirm")
        self.assertEqual(view.reason, "Spam hoặc quảng cáo không được phép")

        confirm = make_interaction(guild, moderator)
        await view.confirm(confirm)
        submitter.assert_awaited_once_with(
            confirm,
            (12, "Spam hoặc quảng cáo không được phép"),
        )
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        confirm.edit_original_response.return_value.delete.assert_not_awaited()

    async def test_invalid_integer_stays_on_form(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        view = make_view()
        open_form = make_interaction(guild, moderator)
        await view.children[0].callback(open_form)
        modal = open_form.response.send_modal.await_args.args[0]
        modal.input._value = "0"

        submit_form = make_interaction(guild, moderator)
        await modal.on_submit(submit_form)
        self.assertEqual(view.step, "field:count")
        submit_form.response.send_message.assert_awaited_once()
        submit_form.response.edit_message.assert_not_awaited()
        view.stop()

    async def test_cancel_never_submits(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        submitter = AsyncMock()
        view = make_view(submitter=submitter)
        interaction = make_interaction(guild, moderator)

        await view.cancel(interaction)

        submitter.assert_not_awaited()
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())
        interaction.response.edit_message.assert_awaited_once()

    async def test_owner_guild_and_live_permission_checks(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        view = make_view(permission_check=lambda _guild, _member: "permission lost")

        stranger = make_interaction(guild, FakeMember(99))
        self.assertFalse(await view.interaction_check(stranger))
        self.assertIn(
            "Chỉ moderator",
            stranger.response.send_message.await_args.args[0],
        )

        wrong_guild = make_interaction(FakeGuild(11), moderator)
        self.assertFalse(await view.interaction_check(wrong_guild))

        denied = make_interaction(guild, moderator)
        self.assertFalse(await view.interaction_check(denied))
        self.assertEqual(
            denied.response.send_message.await_args.args[0],
            "permission lost",
        )
        view.stop()

    async def test_retryable_failure_keeps_confirmation_active(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        submitter = AsyncMock(return_value=ActionResult(False, "retry"))
        view = make_view(submitter=submitter)
        view.values["count"] = FormAnswer(5, "5")
        view.reason = "reason"
        view._show_confirm_step()
        interaction = make_interaction(guild, moderator)

        await view.confirm(interaction)

        self.assertFalse(view.completed)
        self.assertFalse(view.submitting)
        self.assertFalse(view.is_finished())
        interaction.followup.send.assert_awaited_once()
        view.stop()

    async def test_timeout_disables_message_components(self) -> None:
        view = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()

        self.assertTrue(all(item.disabled for item in view.children))
        view.message.edit.assert_awaited_once_with(view=view)
        view.stop()

    async def test_cancel_cannot_race_an_in_flight_confirmation(self) -> None:
        guild = FakeGuild()
        moderator = FakeMember(42)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def submitter(_interaction, _request):
            entered.set()
            await release.wait()
            return ActionResult(True, "completed")

        view = make_view(submitter=submitter)
        view.values["count"] = FormAnswer(5, "5")
        view.reason = "reason"
        view._show_confirm_step()
        confirm_interaction = make_interaction(guild, moderator)
        confirm_task = asyncio.create_task(view.confirm(confirm_interaction))
        await entered.wait()

        cancel_interaction = make_interaction(guild, moderator)
        await view.cancel(cancel_interaction)
        cancel_interaction.response.send_message.assert_awaited_once()
        cancel_interaction.response.edit_message.assert_not_awaited()

        release.set()
        await confirm_task
        self.assertTrue(view.completed)

    async def test_confirmation_embed_stays_within_discord_total_limit(self) -> None:
        fields = tuple(
            ModalField(
                f"field_{index}",
                f"Field {index}",
                ModalInput(title="Field", label="Field"),
            )
            for index in range(5)
        )
        spec = WorkflowSpec(
            namespace="payload-limit",
            title="Payload test",
            action_text="test payload",
            confirm_label="Confirm",
            fields=fields,
        )
        view = ConfigurableModerationView(
            spec=spec,
            author_id=42,
            guild_id=10,
            target=WorkflowTarget(77, "target"),
            submitter=AsyncMock(),
            request_builder=lambda answers, reason: (answers, reason),
            live_permission_check=lambda _guild, _member: None,
            initial_answers={
                field.key: FormAnswer("x" * 2_000, "x" * 2_000)
                for field in fields
            },
        )
        view._show_confirm_step()
        self.assertLessEqual(len(view.build_embed()), 6_000)
        view.stop()

    async def test_reason_select_rejects_too_many_options_with_provided_reason(self) -> None:
        config = ReasonConfig(
            presets=tuple(
                ReasonPreset(str(index), f"Reason {index}", f"Reason {index}")
                for index in range(25)
            )
        )
        spec = WorkflowSpec(
            namespace="reason-limit",
            title="Reason limit",
            action_text="test",
            confirm_label="Confirm",
            reason=config,
        )
        with self.assertRaises(ValueError):
            ConfigurableModerationView(
                spec=spec,
                author_id=42,
                guild_id=10,
                target=None,
                submitter=AsyncMock(),
                request_builder=lambda answers, reason: (answers, reason),
                live_permission_check=lambda _guild, _member: None,
                initial_reason="provided",
            )


if __name__ == "__main__":
    unittest.main()
