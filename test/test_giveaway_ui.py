import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.utils._giveaway_helpers import GiveawayRoleSettings
from cogs.utils._giveaway_ui import (
    GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS,
    BonusMultiplierModal,
    GiveawayBonusRoleSelect,
    GiveawayCreateModal,
    GiveawayCreateView,
    GiveawaySettingsView,
)
from cogs.utils.giveaway import GiveawayCog
from test_giveaway import FakeCollection, FakeDatabase, make_permissions


GUILD_ID = 100
AUTHOR_ID = 42
CHANNEL_ID = 200


def make_interaction(
    *,
    user_id: int = AUTHOR_ID,
    guild_id: int | None = GUILD_ID,
    manage_messages: bool = True,
    administrator: bool = False,
    channel: object | None = None,
) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args: object, **kwargs: object) -> None:
        acknowledged["done"] = True

    return SimpleNamespace(
        guild_id=guild_id,
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=make_permissions(
                administrator=administrator,
                manage_messages=manage_messages,
            ),
        ),
        channel=(
            channel
            if channel is not None
            else SimpleNamespace(id=CHANNEL_ID, send=AsyncMock())
        ),
        response=SimpleNamespace(
            is_done=lambda: acknowledged["done"],
            send_message=AsyncMock(side_effect=acknowledge),
            send_modal=AsyncMock(side_effect=acknowledge),
            defer=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        message=SimpleNamespace(edit=AsyncMock()),
    )


class TestGiveawayCreateUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.collection = FakeCollection()
        self.bot = SimpleNamespace(
            db=FakeDatabase(self.collection),
            is_ready=lambda: False,
            add_view=Mock(),
            command_prefix="!tf ",
            get_channel=Mock(return_value=None),
        )
        self.cog = GiveawayCog(self.bot)
        self.addAsyncCleanup(self.cog.cog_unload)
        self.view = self.make_view()

    def make_view(self) -> GiveawayCreateView:
        view = GiveawayCreateView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            channel_id=CHANNEL_ID,
            prefix="!tf ",
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(view.stop)
        return view

    def make_modal(
        self,
        *,
        prize: str = "Discord Nitro",
        duration: str = "1h",
        winners: str = "1",
    ) -> GiveawayCreateModal:
        modal = GiveawayCreateModal(self.view)
        modal.prize_input._value = prize
        modal.duration_input._value = duration
        modal.winners_input._value = winners
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

    async def test_empty_command_opens_owner_locked_form_panel(self) -> None:
        sent = SimpleNamespace(id=77, edit=AsyncMock())
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID),
            author=SimpleNamespace(
                id=AUTHOR_ID,
                guild_permissions=make_permissions(manage_messages=True),
            ),
            channel=SimpleNamespace(id=CHANNEL_ID),
            clean_prefix="!tf ",
            reply=AsyncMock(return_value=sent),
        )

        await self.cog.giveaway.callback(self.cog, ctx)

        kwargs = ctx.reply.await_args.kwargs
        view = kwargs["view"]
        self.addCleanup(view.stop)
        self.assertIsInstance(view, GiveawayCreateView)
        self.assertIs(view.message, sent)
        self.assertEqual(view.timeout, GIVEAWAY_CREATE_UI_TIMEOUT_SECONDS)
        self.assertEqual(view.author_id, AUTHOR_ID)
        self.assertEqual(view.guild_id, GUILD_ID)
        self.assertIn(view, self.cog._ui_views)
        embed = kwargs["embed"]
        self.assertEqual(embed.title, "🎉 Tạo giveaway")
        self.assertIn("Điền biểu mẫu", embed.description)
        self.assertIn("Cài đặt role", str(embed.to_dict()))
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        labels = [
            item.label for item in view.children if isinstance(item, discord.ui.Button)
        ]
        self.assertEqual(labels, ["Điền biểu mẫu", "Đóng"])
        self.assertEqual(
            sum(isinstance(item, discord.ui.RoleSelect) for item in view.children),
            2,
        )

    async def test_empty_command_shows_usage_for_non_mod(self) -> None:
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID),
            author=SimpleNamespace(
                id=AUTHOR_ID,
                guild_permissions=make_permissions(),
            ),
            channel=SimpleNamespace(id=CHANNEL_ID),
            clean_prefix="!tf ",
            reply=AsyncMock(),
        )

        await self.cog.giveaway.callback(self.cog, ctx)

        self.assertNotIn("view", ctx.reply.await_args.kwargs)
        self.assertIn("giveaway", ctx.reply.await_args.args[0])
        self.assertFalse(self.cog._ui_views)

    async def test_panel_embed_and_modal_stay_within_api_limits(self) -> None:
        embed = self.view.build_embed()
        self.assertLessEqual(len(embed), 6000)
        self.assertLessEqual(len(embed.title or ""), 256)
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertLessEqual(len(embed.fields), 25)
        for field in embed.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)
        self.assertIn("!tf giveaway 10m Nitro Classic", str(embed.to_dict()))
        self.assertIn("giveaway settings", str(embed.to_dict()))

        modal = self.make_modal()
        self.assertLessEqual(len(modal.title), 45)
        self.assertEqual(len(modal.children), 3)
        self.assertEqual(modal.duration_input.default, "1h")
        self.assertEqual(modal.winners_input.default, "1")
        self.assertEqual(modal.prize_input.style, discord.TextStyle.paragraph)
        for field in modal.children:
            self.assertIsInstance(field, discord.ui.Label)
            self.assertLessEqual(len(field.text), 45)
            self.assertLessEqual(len(field.description or ""), 100)
        for item in self.view.children:
            self.assertLessEqual(len(item.custom_id or ""), 100)
            self.assertFalse(item.disabled)

    async def test_only_opening_mod_in_original_guild_passes_check(self) -> None:
        self.assertTrue(await self.view.interaction_check(make_interaction()))
        for args in (
            {"user_id": AUTHOR_ID + 1},
            {"guild_id": GUILD_ID + 1},
            {"guild_id": None},
            {"manage_messages": False},
        ):
            with self.subTest(args=args):
                interaction = make_interaction(**args)
                self.assertFalse(await self.view.interaction_check(interaction))
                self.assert_private_reply(interaction)

    async def test_open_form_sends_modal_for_owner(self) -> None:
        interaction = make_interaction()
        await self.view.open_form.callback(interaction)
        interaction.response.send_modal.assert_awaited_once()
        modal = interaction.response.send_modal.await_args.args[0]
        self.addCleanup(modal.stop)
        self.assertIsInstance(modal, GiveawayCreateModal)
        self.assertEqual(modal.title, "Tạo giveaway")

    async def test_open_form_rejects_outsiders_without_sending_modal(self) -> None:
        interaction = make_interaction(user_id=AUTHOR_ID + 1)
        await self.view.open_form.callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        self.assert_private_reply(interaction)

    async def test_modal_creates_giveaway_and_closes_panel(self) -> None:
        posted = SimpleNamespace(
            id=999,
            jump_url="https://discord.com/channels/100/200/999",
        )
        self.cog.start_giveaway = AsyncMock(
            return_value=(posted, {"prize": "Discord Nitro"}),
        )
        interaction = make_interaction()
        modal = self.make_modal(prize="  Discord Nitro  ", duration="10m", winners="3")

        await modal.on_submit(interaction)

        self.cog.start_giveaway.assert_awaited_once()
        kwargs = self.cog.start_giveaway.await_args.kwargs
        self.assertEqual(kwargs["prize"], "Discord Nitro")
        self.assertEqual(kwargs["winner_count"], 3)
        self.assertEqual(kwargs["seconds"], 600)
        self.assertEqual(kwargs["host_id"], AUTHOR_ID)
        self.assertEqual(kwargs["guild_id"], GUILD_ID)
        self.assertEqual(kwargs["settings"], self.view.settings)
        interaction.response.defer.assert_awaited_once()
        self.assertTrue(interaction.response.defer.await_args.kwargs["ephemeral"])
        reply = self.assert_private_reply(interaction)
        self.assertIn("Discord Nitro", reply)
        self.assertIn(posted.jump_url, reply)
        self.view.message.edit.assert_awaited_once()
        edited = self.view.message.edit.await_args.kwargs["embed"]
        self.assertEqual(edited.title, "🎉 Đã tạo giveaway")
        self.assertTrue(self.view.is_finished())
        for item in self.view.children:
            self.assertTrue(item.disabled)

    async def test_invalid_modal_values_do_not_create_giveaway(self) -> None:
        self.cog.start_giveaway = AsyncMock()
        for args in (
            {"prize": "   "},
            {"duration": "abc"},
            {"duration": "9s"},
            {"duration": "31d"},
            {"winners": "0"},
            {"winners": "21"},
            {"winners": "abc"},
        ):
            with self.subTest(args=args):
                modal = self.make_modal(**args)
                interaction = make_interaction()
                await modal.on_submit(interaction)
                self.assert_private_reply(interaction)
                interaction.response.defer.assert_not_awaited()
        self.cog.start_giveaway.assert_not_awaited()
        self.view.message.edit.assert_not_awaited()
        self.assertFalse(self.view.is_finished())

    async def test_modal_rechecks_identity_before_creating(self) -> None:
        self.cog.start_giveaway = AsyncMock()
        for args in (
            {"user_id": AUTHOR_ID + 1},
            {"guild_id": GUILD_ID + 1},
            {"guild_id": None},
            {"manage_messages": False},
        ):
            with self.subTest(args=args):
                interaction = make_interaction(**args)
                await self.make_modal().on_submit(interaction)
                self.assert_private_reply(interaction)
        self.cog.start_giveaway.assert_not_awaited()

    async def test_close_panel_disables_controls(self) -> None:
        interaction = make_interaction()
        await self.view.close_panel.callback(interaction)
        interaction.response.edit_message.assert_awaited_once()
        self.assertTrue(self.view.is_finished())
        for item in self.view.children:
            self.assertTrue(item.disabled)

    async def test_cog_unload_stops_open_create_views(self) -> None:
        view = self.view
        self.cog._ui_views.add(view)
        self.cog.cog_unload()
        self.assertTrue(view.is_finished())
        self.assertFalse(self.cog._ui_views)

    async def test_create_panel_role_select_updates_draft_without_saving_guild(self) -> None:
        select = next(
            item
            for item in self.view.children
            if isinstance(item, GiveawayBonusRoleSelect)
        )
        select._values = [SimpleNamespace(id=22)]
        interaction = make_interaction()
        await select.callback(interaction)
        self.assertEqual(self.view.settings.bonus_role_ids, (22,))
        self.assertEqual(self.cog.get_guild_settings(GUILD_ID).bonus_role_ids, ())
        interaction.response.edit_message.assert_awaited_once()

    async def test_settings_command_opens_persisting_panel(self) -> None:
        sent = SimpleNamespace(id=88, edit=AsyncMock())
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=GUILD_ID),
            author=SimpleNamespace(
                id=AUTHOR_ID,
                guild_permissions=make_permissions(manage_messages=True),
            ),
            channel=SimpleNamespace(id=CHANNEL_ID),
            clean_prefix="!tf ",
            reply=AsyncMock(return_value=sent),
        )

        await self.cog.giveaway_settings.callback(self.cog, ctx)

        view = ctx.reply.await_args.kwargs["view"]
        self.addCleanup(view.stop)
        self.assertIsInstance(view, GiveawaySettingsView)
        self.assertTrue(view.persist_role_settings)
        self.assertEqual(ctx.reply.await_args.kwargs["embed"].title, "⚙️ Cài đặt giveaway")
        self.assertIn(view, self.cog._ui_views)

    async def test_settings_panel_saves_roles_and_custom_multiplier(self) -> None:
        view = GiveawaySettingsView(
            self.cog,
            guild_id=GUILD_ID,
            author_id=AUTHOR_ID,
            prefix="!tf ",
            settings=GiveawayRoleSettings(),
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        self.addCleanup(view.stop)
        bonus = next(
            item for item in view.children if isinstance(item, GiveawayBonusRoleSelect)
        )
        bonus._values = [SimpleNamespace(id=33)]
        await bonus.callback(make_interaction())
        self.assertEqual(self.cog.get_guild_settings(GUILD_ID).bonus_role_ids, (33,))

        modal = BonusMultiplierModal(view)
        modal.multiplier_input._value = "7"
        interaction = make_interaction()
        await modal.on_submit(interaction)
        self.assertEqual(view.settings.bonus_multiplier, 7)
        self.assertEqual(self.cog.get_guild_settings(GUILD_ID).bonus_multiplier, 7)
        self.assertIn("x7", self.assert_private_reply(interaction))
