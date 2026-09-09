import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from cogs.operation import bot_status as status_module
from cogs.operation._bot_status_ui import BotStatusModal, BotStatusView
from cogs.operation.bot_status import BotStatusCog, make_override_status


GUILD_ID = 123
AUTHOR_ID = 42


def make_interaction(
    *,
    user_id: int = AUTHOR_ID,
    guild_id: int | None = GUILD_ID,
    administrator: bool = True,
) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    return SimpleNamespace(
        guild_id=guild_id,
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=discord.Permissions(administrator=administrator),
        ),
        response=SimpleNamespace(
            is_done=lambda: acknowledged["done"],
            send_message=AsyncMock(side_effect=acknowledge),
            send_modal=AsyncMock(side_effect=acknowledge),
            defer=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(edit=AsyncMock()),
    )


class TestBotStatusUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = 1000.0
        self.ready = asyncio.Event()
        self.bot = SimpleNamespace(
            wait_until_ready=self.ready.wait,
            is_ready=lambda: True,
            is_closed=lambda: False,
            change_presence=AsyncMock(),
        )
        for patcher in (
            patch.object(status_module, "time", SimpleNamespace(monotonic=lambda: self.now)),
            patch.object(
                status_module,
                "load_statuses",
                return_value=[
                    {"type": "PLAYING", "text": "first random"},
                    {"type": "WATCHING", "text": "second random"},
                ],
            ),
            patch.object(status_module.random, "choice", side_effect=lambda values: values[0]),
            patch.object(status_module.random, "uniform", return_value=600),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cog = BotStatusCog(self.bot)
        self.addAsyncCleanup(self.cog.cog_unload)
        self.view = self.make_view()

    def make_view(self) -> BotStatusView:
        view = BotStatusView(
            self.cog, guild_id=GUILD_ID, author_id=AUTHOR_ID, prefix="!tf "
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    def make_modal(
        self,
        activity_type: str = "PLAYING",
        *,
        text: str = "Fortnite with friends",
        duration: str = "1h",
    ) -> BotStatusModal:
        modal = BotStatusModal(self.view, activity_type)
        modal.text_input._value = text
        modal.duration_input._value = duration
        self.addCleanup(modal.stop)
        return modal

    def assert_private_reply(self, interaction: SimpleNamespace) -> str:
        replies = (
            interaction.response.send_message.await_args_list
            + interaction.followup.send.await_args_list
        )
        self.assertTrue(replies, "Interaction did not receive a private response")
        contents = []
        for reply in replies:
            self.assertTrue(reply.kwargs.get("ephemeral"))
            self.assertEqual(
                reply.kwargs["allowed_mentions"].to_dict(),
                discord.AllowedMentions.none().to_dict(),
            )
            contents.append(reply.args[0] if reply.args else reply.kwargs.get("content", ""))
        return "\n".join(contents)

    def assert_no_status_change(self) -> None:
        self.bot.change_presence.assert_not_awaited()
        self.assertIsNone(self.cog.current_status)
        self.assertIsNone(self.cog.override)

    async def test_view_has_six_activity_types_and_bounded_components(self) -> None:
        self.assertEqual(self.view.timeout, 180)
        selects = [item for item in self.view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(selects), 1)
        self.assertEqual(
            {option.value for option in selects[0].options},
            status_module.VALID_ACTIVITY_TYPES,
        )
        buttons = [item for item in self.view.children if isinstance(item, discord.ui.Button)]
        self.assertEqual(len(buttons), 3)
        for item in self.view.children:
            self.assertLessEqual(len(item.custom_id), 100)
            self.assertFalse(item.disabled)
        embed = await self.view.build_embed()
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertLessEqual(len(embed.fields), 25)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)
        self.assertIn("mọi server", str(embed.to_dict()))
        self.assert_no_status_change()

    async def test_only_opening_admin_in_original_guild_passes_check(self) -> None:
        self.assertTrue(await self.view.interaction_check(make_interaction()))
        for args in (
            {"user_id": AUTHOR_ID + 1},
            {"guild_id": GUILD_ID + 1},
            {"guild_id": None},
            {"administrator": False},
        ):
            with self.subTest(args=args):
                interaction = make_interaction(**args)
                self.assertFalse(await self.view.interaction_check(interaction))
                self.assert_private_reply(interaction)
        self.assert_no_status_change()

    async def test_every_callback_rechecks_permissions_before_acting(self) -> None:
        self.view.select_activity._values = ["PLAYING"]
        for name in ("select_activity", "reset_status", "refresh_status", "close_panel"):
            for args in (
                {"user_id": AUTHOR_ID + 1},
                {"guild_id": GUILD_ID + 1},
                {"guild_id": None},
                {"administrator": False},
            ):
                with self.subTest(callback=name, args=args):
                    interaction = make_interaction(**args)
                    await getattr(self.view, name).callback(interaction)
                    self.assert_private_reply(interaction)
                    interaction.response.send_modal.assert_not_awaited()
                    interaction.response.edit_message.assert_not_awaited()
        self.view.message.edit.assert_not_awaited()
        self.assertFalse(self.view.is_finished())
        self.assert_no_status_change()

    async def test_select_opens_modal_without_changing_presence_or_cooldown(self) -> None:
        for activity_type in sorted(status_module.VALID_ACTIVITY_TYPES):
            with self.subTest(activity_type=activity_type):
                self.view.select_activity._values = [activity_type]
                interaction = make_interaction()

                await self.view.select_activity.callback(interaction)

                interaction.response.send_modal.assert_awaited_once()
                modal = interaction.response.send_modal.await_args.args[0]
                self.addCleanup(modal.stop)
                self.assertIsInstance(modal, BotStatusModal)
                self.assertEqual(len(modal.children), 2)
                self.assertEqual(modal.duration_input.default, "1h")
                self.assertEqual(modal.text_input.style, discord.TextStyle.short)
                self.assertLessEqual(len(modal.title), 45)
                for field in modal.children:
                    self.assertLessEqual(len(field.label), 45)
                    self.assertTrue(field.required)
        self.assert_no_status_change()
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 60)
        self.bot.change_presence.assert_awaited_once()

    async def test_modal_applies_trimmed_multiword_status_and_refreshes_panel(self) -> None:
        modal = self.make_modal(text="  Fortnite with friends  ", duration=" 2h ")
        interaction = make_interaction()

        async def change_presence(**kwargs: object) -> None:
            self.assertTrue(interaction.response.is_done())

        self.bot.change_presence.side_effect = change_presence
        await modal.on_submit(interaction)

        self.assertEqual(
            self.cog.current_status,
            {"type": "PLAYING", "text": "Fortnite with friends"},
        )
        self.assertEqual(self.cog.override.deadline, self.now + 7200)
        interaction.response.defer.assert_awaited_once()
        self.assertTrue(interaction.response.defer.await_args.kwargs["ephemeral"])
        reply = self.assert_private_reply(interaction)
        self.assertIn("Fortnite with friends", reply)
        self.assertIn("mọi server", reply)
        self.view.message.edit.assert_awaited_once()
        self.assertIn("Fortnite with friends", str((await self.view.build_embed()).to_dict()))

    async def test_custom_modal_uses_custom_activity_schema(self) -> None:
        modal = self.make_modal("CUSTOM", text="  Đang nghĩ về bữa tối  ", duration="1d")
        interaction = make_interaction()

        await modal.on_submit(interaction)

        self.assertEqual(
            self.cog.current_status,
            {"type": "CUSTOM", "think": "Đang nghĩ về bữa tối"},
        )
        activity = self.bot.change_presence.await_args.kwargs["activity"]
        self.assertIsInstance(activity, discord.CustomActivity)
        self.assertEqual(activity.name, "Đang nghĩ về bữa tối")
        self.assertEqual(self.cog.override.deadline, self.now + 86400)
        self.assert_private_reply(interaction)

    async def test_invalid_modal_values_do_not_mutate_or_consume_cooldown(self) -> None:
        for args in (
            {"duration": "30s"},
            {"duration": "1h30m"},
            {"duration": "0m"},
            {"duration": "25h"},
            {"text": "   "},
            {"text": "x" * 129},
            {"text": "first\nsecond"},
            {"text": "first\rsecond"},
        ):
            with self.subTest(args=args):
                modal = self.make_modal(**args)
                interaction = make_interaction()
                await modal.on_submit(interaction)
                self.assert_private_reply(interaction)
                self.assert_no_status_change()
        self.view.message.edit.assert_not_awaited()
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 60)
        self.bot.change_presence.assert_awaited_once()

    async def test_modal_rechecks_identity_guild_and_current_admin_permissions(self) -> None:
        for args in (
            {"user_id": AUTHOR_ID + 1},
            {"guild_id": GUILD_ID + 1},
            {"guild_id": None},
            {"administrator": False},
        ):
            with self.subTest(args=args):
                modal = self.make_modal()
                interaction = make_interaction(**args)
                await modal.on_submit(interaction)
                self.assert_private_reply(interaction)
                self.assert_no_status_change()

    async def test_ui_and_prefix_commands_share_mutation_cooldown(self) -> None:
        context = SimpleNamespace(send=AsyncMock(), clean_prefix="!tf ")
        await self.cog.set_bot_status.callback(
            self.cog, context, "PLAYING", "1h", text="From prefix"
        )
        original = self.cog.override
        self.bot.change_presence.reset_mock()

        for operation in (self.make_modal().on_submit, self.view.reset_status.callback):
            interaction = make_interaction()
            await operation(interaction)
            reply = self.assert_private_reply(interaction)
            self.assertIn("giây", reply)
            self.assertIs(self.cog.override, original)
        self.bot.change_presence.assert_not_awaited()
        self.view.message.edit.assert_not_awaited()

    async def test_successful_modal_replaces_current_override_after_cooldown(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "Old status"), 60)
        previous = self.cog.override
        self.now += 11

        await self.make_modal("WATCHING", text="New movie", duration="30m").on_submit(
            make_interaction()
        )

        self.assertIsNot(self.cog.override, previous)
        self.assertEqual(self.cog.override.status, {"type": "WATCHING", "text": "New movie"})
        self.assertEqual(self.cog.override.deadline, self.now + 1800)

    async def test_failed_modal_presence_update_preserves_previous_override(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "Old status"), 60)
        previous = self.cog.override
        self.now += 11
        self.bot.change_presence.side_effect = RuntimeError("Discord disconnected")
        interaction = make_interaction()

        await self.make_modal().on_submit(interaction)

        self.assertIs(self.cog.override, previous)
        self.assertEqual(self.cog.current_status, previous.status)
        reply = self.assert_private_reply(interaction)
        self.assertIn("Không thể", reply)
        self.assertNotIn("Đã đặt", reply)
        self.view.message.edit.assert_not_awaited()

    async def test_reset_restores_random_rotation_using_shared_service(self) -> None:
        await self.cog.update_status()
        last_random = self.cog.last_status
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 3600)
        self.now += 11
        interaction = make_interaction()

        async def change_presence(**kwargs: object) -> None:
            self.assertTrue(interaction.response.is_done())

        self.bot.change_presence.side_effect = change_presence
        await self.view.reset_status.callback(interaction)

        self.assertIsNone(self.cog.override)
        self.assertNotEqual(self.cog.current_status, last_random)
        self.assertEqual(self.cog.current_status, self.cog.last_status)
        self.assert_private_reply(interaction)
        self.view.message.edit.assert_awaited_once()

    async def test_reset_without_override_reports_no_change(self) -> None:
        interaction = make_interaction()

        await self.view.reset_status.callback(interaction)

        self.assertIn("không có", self.assert_private_reply(interaction))
        self.assert_no_status_change()

    async def test_failed_reset_preserves_override_and_reports_failure(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 3600)
        previous = self.cog.override
        self.now += 11
        self.bot.change_presence.side_effect = OSError("Connection unavailable")
        interaction = make_interaction()

        await self.view.reset_status.callback(interaction)

        self.assertIs(self.cog.override, previous)
        self.assertEqual(self.cog.current_status, previous.status)
        self.assertIn("Không thể", self.assert_private_reply(interaction))
        self.view.message.edit.assert_not_awaited()

    async def test_refresh_reads_latest_global_state_without_presence_write(self) -> None:
        original_embed = await self.view.build_embed()
        await self.cog.set_override(make_override_status("WATCHING", "Another admin's movie"), 60)
        self.bot.change_presence.reset_mock()
        interaction = make_interaction()

        await self.view.refresh_status.callback(interaction)

        self.bot.change_presence.assert_not_awaited()
        self.assertNotEqual(original_embed.to_dict(), (await self.view.build_embed()).to_dict())
        edits = (
            interaction.response.edit_message.await_args_list
            + interaction.edit_original_response.await_args_list
            + self.view.message.edit.await_args_list
        )
        self.assertTrue(edits)
        self.assertIn("Another admin's movie", str(edits[-1].kwargs["embed"].to_dict()))

    async def test_close_disables_panel_without_clearing_override_and_rejects_stale_modal(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 3600)
        previous = self.cog.override
        modal = self.make_modal()
        self.bot.change_presence.reset_mock()

        await self.view.close_panel.callback(make_interaction())

        self.assertTrue(self.view.is_finished())
        self.assertTrue(all(item.disabled for item in self.view.children))
        self.assertIs(self.cog.override, previous)
        interaction = make_interaction()
        await modal.on_submit(interaction)
        self.assert_private_reply(interaction)
        self.bot.change_presence.assert_not_awaited()
        self.assertIs(self.cog.override, previous)

    async def test_timeout_disables_panel_without_reset_and_rejects_stale_modal(self) -> None:
        await self.cog.set_override(make_override_status("PLAYING", "Fortnite"), 3600)
        previous = self.cog.override
        modal = self.make_modal()
        self.bot.change_presence.reset_mock()

        await self.view.on_timeout()

        self.assertTrue(self.view.is_finished())
        self.assertTrue(all(item.disabled for item in self.view.children))
        self.view.message.edit.assert_awaited_once()
        interaction = make_interaction()
        await modal.on_submit(interaction)
        self.assert_private_reply(interaction)
        self.bot.change_presence.assert_not_awaited()
        self.assertIs(self.cog.override, previous)

    async def test_timeout_handles_deleted_panel_message(self) -> None:
        self.view.message.edit.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Message"
        )

        await self.view.on_timeout()

        self.assertTrue(self.view.is_finished())
        self.assertTrue(all(item.disabled for item in self.view.children))
        self.assert_no_status_change()

    async def test_unloaded_cog_rejects_controls_and_pending_modal(self) -> None:
        modal = self.make_modal()
        self.cog._unloading = True
        self.view.select_activity._values = ["PLAYING"]

        for operation in (
            self.view.select_activity.callback,
            self.view.reset_status.callback,
            self.view.refresh_status.callback,
            self.view.close_panel.callback,
            modal.on_submit,
        ):
            with self.subTest(operation=operation):
                interaction = make_interaction()
                await operation(interaction)
                self.assert_private_reply(interaction)
                interaction.response.send_modal.assert_not_awaited()
        self.assert_no_status_change()

    async def test_mention_text_is_suppressed_in_private_reply_and_panel_refresh(self) -> None:
        interaction = make_interaction()

        await self.make_modal(text="@everyone <@123> Fortnite").on_submit(interaction)

        self.assert_private_reply(interaction)
        for edited in self.view.message.edit.await_args_list:
            self.assertEqual(
                edited.kwargs["allowed_mentions"].to_dict(),
                discord.AllowedMentions.none().to_dict(),
            )


if __name__ == "__main__":
    unittest.main()
