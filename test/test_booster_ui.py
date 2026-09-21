import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.booster._custom_resource_ui import (
    BOOSTER_UI_TIMEOUT_SECONDS,
    GRADIENT_ROLE_COLOR_PRESETS,
    ROLE_GRADIENT_SELECT_CUSTOM_ID,
    ROLE_SOLID_SELECT_CUSTOM_ID,
    ROOM_LIMIT_OPTIONS,
    ROOM_LIMIT_SELECT_CUSTOM_ID,
    SOLID_ROLE_COLOR_PRESETS,
    BoosterActionResult,
    BoosterRoleEditorView,
    BoosterRoomCreatorView,
    RoleDesignDraft,
    RoleDesignModal,
    RoomDesignDraft,
    RoomNameModal,
)
from cogs.booster._role_colors import RoleColorSpec
from cogs.booster.create_custom_role import BoosterCustomRoleCog
from cogs.booster.create_custom_room import BoosterCustomRoomCog
from cogs.booster.update_custom_role import BoosterCustomRoleUpdateCog
from test_shop import FakeCollection


def make_interaction(user_id: int = 42, guild_id: int = 10):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild=SimpleNamespace(id=guild_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def make_context():
    return SimpleNamespace(
        guild=SimpleNamespace(id=10),
        author=SimpleNamespace(id=42),
        message=SimpleNamespace(attachments=[]),
        clean_prefix="!tf ",
        send=AsyncMock(),
        reply=AsyncMock(),
    )


def make_bot():
    shop_roles = Mock()
    shop_roles.find_one.return_value = None
    return SimpleNamespace(
        db={
            "booster_custom_roles": Mock(),
            "booster_custom_rooms": Mock(),
            "shop_custom_roles": shop_roles,
            "shop_custom_rooms": FakeCollection(),
            "shop_inventory": FakeCollection(),
            "shop_migrations": FakeCollection(),
        },
        user=SimpleNamespace(id=999),
        global_vars={},
    )


class TestBoosterViews(unittest.IsolatedAsyncioTestCase):
    async def test_role_view_structure_and_owner_lock(self) -> None:
        view = BoosterRoleEditorView(
            author_id=42,
            command_name="custom_role",
            submitter=AsyncMock(),
        )

        self.assertEqual(view.timeout, BOOSTER_UI_TIMEOUT_SECONDS)
        self.assertEqual(
            view.children,
            [
                view.solid_select,
                view.gradient_select,
                view.custom_solid_button,
                view.custom_gradient_button,
                view.confirm_button,
                view.cancel_button,
            ],
        )
        self.assertIsInstance(view.solid_select, discord.ui.Select)
        self.assertIsInstance(view.gradient_select, discord.ui.Select)
        self.assertEqual(
            view.solid_select.custom_id,
            ROLE_SOLID_SELECT_CUSTOM_ID,
        )
        self.assertEqual(
            view.gradient_select.custom_id,
            ROLE_GRADIENT_SELECT_CUSTOM_ID,
        )
        self.assertEqual(
            [option.value for option in view.solid_select.options],
            [preset.key for preset in SOLID_ROLE_COLOR_PRESETS],
        )
        self.assertEqual(
            [option.value for option in view.gradient_select.options],
            [preset.key for preset in GRADIENT_ROLE_COLOR_PRESETS],
        )
        self.assertTrue(view.confirm_button.disabled)
        self.assertEqual(view.confirm_button.custom_id, "booster-role:confirm")
        self.assertEqual(view.cancel_button.custom_id, "booster-role:cancel")

        owner = make_interaction(42)
        stranger = make_interaction(99)
        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once_with(
            "Chỉ Booster đã mở bảng này mới có thể sử dụng.",
            ephemeral=True,
        )
        view.stop()

    async def test_room_view_structure_and_owner_lock(self) -> None:
        view = BoosterRoomCreatorView(author_id=42, submitter=AsyncMock())

        self.assertEqual(
            view.children,
            [
                view.limit_select,
                view.name_button,
                view.confirm_button,
                view.cancel_button,
            ],
        )
        self.assertIsInstance(view.limit_select, discord.ui.Select)
        self.assertEqual(view.limit_select.custom_id, ROOM_LIMIT_SELECT_CUSTOM_ID)
        self.assertEqual(
            [option.value for option in view.limit_select.options],
            [str(limit) for limit, _, _ in ROOM_LIMIT_OPTIONS],
        )
        self.assertEqual(
            [option.value for option in view.limit_select.options if option.default],
            ["0"],
        )
        self.assertEqual(view.name_button.custom_id, "booster-room:name")
        self.assertEqual(view.confirm_button.custom_id, "booster-room:confirm")
        self.assertTrue(view.confirm_button.disabled)

        stranger = make_interaction(99)
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once_with(
            "Chỉ Booster đã mở bảng này mới có thể sử dụng.",
            ephemeral=True,
        )
        view.stop()

    async def test_role_preset_opens_prefilled_modal_and_valid_submit_previews(
        self,
    ) -> None:
        view = BoosterRoleEditorView(
            author_id=42,
            command_name="custom_role",
            submitter=AsyncMock(),
        )
        select_interaction = make_interaction()
        view.gradient_select._values = ["ocean"]

        await view.gradient_select.callback(select_interaction)

        select_interaction.response.send_modal.assert_awaited_once()
        modal = select_interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, RoleDesignModal)
        self.assertTrue(modal.gradient)
        self.assertEqual(modal.primary_color.value, "#2193B0")
        self.assertEqual(modal.secondary_color.value, "#6DD5ED")

        modal.role_name._value = "  **Ocean VIP** @everyone  "
        modal.primary_color._value = "#112233"
        modal.secondary_color._value = "#445566"
        submit_interaction = make_interaction()
        await modal.on_submit(submit_interaction)

        self.assertEqual(view.draft.role_name, "**Ocean VIP** @everyone")
        self.assertEqual(view.draft.color_spec.primary.value, 0x112233)
        self.assertEqual(view.draft.color_spec.secondary.value, 0x445566)
        self.assertFalse(view.confirm_button.disabled)
        kwargs = submit_interaction.response.edit_message.await_args.kwargs
        fields = {field.name: field.value for field in kwargs["embed"].fields}
        self.assertEqual(fields["Màu"], "`#112233 → #445566`")
        self.assertNotIn("@everyone", fields["Tên role"])
        self.assertIs(kwargs["view"], view)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        view.stop()

    async def test_room_limit_and_name_update_preview(self) -> None:
        view = BoosterRoomCreatorView(author_id=42, submitter=AsyncMock())
        view.limit_select._values = ["10"]
        limit_interaction = make_interaction()

        await view.limit_select.callback(limit_interaction)

        self.assertEqual(view.user_limit, 10)
        self.assertEqual(
            [option.value for option in view.limit_select.options if option.default],
            ["10"],
        )
        limit_embed = limit_interaction.response.edit_message.await_args.kwargs[
            "embed"
        ]
        self.assertEqual(
            {field.name: field.value for field in limit_embed.fields}["Giới hạn"],
            "10 người",
        )

        modal = RoomNameModal(view)
        modal.room_name._value = "  **Phòng Chill**  "
        name_interaction = make_interaction()
        await modal.on_submit(name_interaction)

        self.assertEqual(view.draft, RoomDesignDraft("**Phòng Chill**", 10))
        self.assertFalse(view.confirm_button.disabled)
        embed = name_interaction.response.edit_message.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Tên phòng"], "\\*\\*Phòng Chill\\*\\*")
        self.assertEqual(fields["Giới hạn"], "10 người")
        view.stop()

    async def test_successful_and_retryable_confirm_lifecycle(self) -> None:
        success_submitter = AsyncMock(
            return_value=BoosterActionResult(True, "Đã tạo <@&123>.")
        )
        color_spec = RoleColorSpec(discord.Color(0x112233))
        role_view = BoosterRoleEditorView(
            author_id=42,
            command_name="custom_role",
            submitter=success_submitter,
            default_role_name="VIP",
            initial_color_spec=color_spec,
        )
        success_interaction = make_interaction()

        await role_view.confirm_button.callback(success_interaction)

        success_submitter.assert_awaited_once_with(
            success_interaction,
            RoleDesignDraft("VIP", color_spec),
        )
        success_interaction.response.defer.assert_awaited_once_with()
        self.assertTrue(role_view.completed)
        self.assertTrue(role_view.is_finished())
        self.assertTrue(all(item.disabled for item in role_view.children))
        success_kwargs = (
            success_interaction.edit_original_response.await_args.kwargs
        )
        self.assertEqual(success_kwargs["content"], "Đã tạo <@&123>.")
        self.assertFalse(success_kwargs["allowed_mentions"].roles)

        retry_submitter = AsyncMock(
            return_value=BoosterActionResult(False, "Hãy thử lại.")
        )
        room_view = BoosterRoomCreatorView(
            author_id=42,
            submitter=retry_submitter,
        )
        room_view.draft = RoomDesignDraft("Phòng", 5)
        room_view.confirm_button.disabled = False
        retry_interaction = make_interaction()

        await room_view.confirm_button.callback(retry_interaction)

        retry_submitter.assert_awaited_once_with(
            retry_interaction,
            room_view.draft,
        )
        self.assertFalse(room_view.completed)
        self.assertFalse(room_view.submitting)
        self.assertFalse(room_view.is_finished())
        self.assertTrue(all(not item.disabled for item in room_view.children))
        retry_interaction.followup.send.assert_awaited_once()
        args = retry_interaction.followup.send.await_args.args
        kwargs = retry_interaction.followup.send.await_args.kwargs
        self.assertEqual(args[0], "Hãy thử lại.")
        self.assertTrue(kwargs["ephemeral"])
        self.assertFalse(kwargs["allowed_mentions"].roles)
        retry_interaction.edit_original_response.assert_not_awaited()
        room_view.stop()


class TestBoosterCommandDispatch(unittest.IsolatedAsyncioTestCase):
    def test_command_names_aliases_and_optional_signatures(self) -> None:
        role_command = BoosterCustomRoleCog.custom_role
        update_command = BoosterCustomRoleUpdateCog.update_custom_role
        room_command = BoosterCustomRoomCog.custom_room

        self.assertEqual(role_command.name, "custom_role")
        self.assertEqual(role_command.aliases, ["booster_role"])
        self.assertEqual(update_command.name, "update_custom_role")
        self.assertEqual(
            update_command.aliases,
            ["customroleupdate", "boosterroleupdate"],
        )
        self.assertEqual(room_command.name, "custom_room")
        self.assertEqual(room_command.aliases, ["booster_room"])
        for command in (role_command, update_command):
            parameters = inspect.signature(command.callback).parameters
            self.assertIsNone(parameters["color_hex"].default)
            self.assertIsNone(parameters["role_name"].default)
            self.assertEqual(
                parameters["role_name"].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )
        room_name = inspect.signature(room_command.callback).parameters[
            "room_name"
        ]
        self.assertIsNone(room_name.default)
        self.assertEqual(room_name.kind, inspect.Parameter.KEYWORD_ONLY)

    async def test_custom_role_legacy_and_zero_arg_dispatch(self) -> None:
        cog = BoosterCustomRoleCog(make_bot())
        cog._open_role_editor = AsyncMock()
        cog._create_custom_role = AsyncMock(
            return_value=BoosterActionResult(True, "role-created")
        )
        zero_ctx = make_context()
        await cog.custom_role.callback(cog, zero_ctx)
        cog._open_role_editor.assert_awaited_once_with(zero_ctx, None)
        cog._create_custom_role.assert_not_awaited()

        legacy_ctx = make_context()
        await cog.custom_role.callback(
            cog,
            legacy_ctx,
            "#112233",
            role_name="#445566 Legacy Role",
        )
        kwargs = cog._create_custom_role.await_args.kwargs
        self.assertEqual(kwargs["role_name"], "Legacy Role")
        self.assertEqual(kwargs["color_spec"].primary.value, 0x112233)
        self.assertEqual(kwargs["color_spec"].secondary.value, 0x445566)
        legacy_ctx.send.assert_awaited_once()
        self.assertFalse(
            legacy_ctx.send.await_args.kwargs["allowed_mentions"].roles
        )

    async def test_update_role_legacy_and_zero_arg_dispatch(self) -> None:
        cog = BoosterCustomRoleUpdateCog(make_bot())
        cog._open_role_editor = AsyncMock()
        cog._update_custom_role = AsyncMock(
            return_value=BoosterActionResult(True, "role-updated")
        )
        zero_ctx = make_context()
        await cog.update_custom_role.callback(cog, zero_ctx)
        cog._open_role_editor.assert_awaited_once_with(zero_ctx, None)

        legacy_ctx = make_context()
        await cog.update_custom_role.callback(
            cog,
            legacy_ctx,
            "#ABCDEF",
            role_name="Legacy Role",
        )
        kwargs = cog._update_custom_role.await_args.kwargs
        self.assertEqual(kwargs["role_name"], "Legacy Role")
        self.assertEqual(kwargs["color_spec"].primary.value, 0xABCDEF)
        self.assertIsNone(kwargs["color_spec"].secondary)

    async def test_custom_room_legacy_and_zero_arg_dispatch(self) -> None:
        cog = BoosterCustomRoomCog(make_bot())
        cog._open_room_creator = AsyncMock()
        cog._create_custom_room = AsyncMock(
            return_value=BoosterActionResult(True, "room-created")
        )
        zero_ctx = make_context()
        await cog.custom_room.callback(cog, zero_ctx)
        cog._open_room_creator.assert_awaited_once_with(zero_ctx)
        cog._create_custom_room.assert_not_awaited()

        legacy_ctx = make_context()
        await cog.custom_room.callback(
            cog,
            legacy_ctx,
            room_name="Legacy Room",
        )
        cog._create_custom_room.assert_awaited_once_with(
            guild=legacy_ctx.guild,
            member=legacy_ctx.author,
            room_name="Legacy Room",
        )
        legacy_ctx.send.assert_awaited_once()
        self.assertFalse(
            legacy_ctx.send.await_args.kwargs["allowed_mentions"].roles
        )


if __name__ == "__main__":
    unittest.main()
