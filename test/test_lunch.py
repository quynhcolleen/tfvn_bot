import asyncio
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands
from PIL import Image

from cogs.utils._lunch_helpers import Food, LunchFilters
from cogs.utils._lunch_media import WishAnimation
from cogs.utils.lunch import BudgetModal, LunchCog, LunchView


FOODS = (
    Food(0, "Cơm tấm", "Sườn bì chả", 45, quip="Ăn ngon nhé!"),
    Food(7, "Cơm chay", "Rau và đậu hũ", 35, veg=True, quip="Món xanh hôm nay."),
    Food(8, "Bún chay", "Bún và rau", 50, veg=True),
    Food(4, "Sushi", "Cá hồi", 150),
)


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args, **kwargs):
        acknowledged["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild=None,
        response=SimpleNamespace(
            defer=AsyncMock(side_effect=acknowledge),
            send_message=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
            send_modal=AsyncMock(side_effect=acknowledge),
            is_done=lambda: acknowledged["done"],
        ),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def http_error() -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=500, reason="Server error"), "Temporary failure",
    )


class TestLunchUI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.gif_path = Path(directory.name) / "wish.gif"
        self.gif_path.write_bytes(b"GIF89a")
        output = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(output, format="PNG")
        self.media = MagicMock()
        self.media.wish.return_value = WishAnimation(self.gif_path, 0.3)
        self.media.food_png.return_value = output.getvalue()
        with patch("cogs.utils.lunch.load_foods", return_value=FOODS), patch(
            "cogs.utils.lunch.LunchMedia", return_value=self.media,
        ):
            self.cog = LunchCog(SimpleNamespace())
        self.addAsyncCleanup(self.cog.cog_unload)

    def view(self, filters: LunchFilters = LunchFilters(), user_id: int = 42):
        view = LunchView(self.cog, user_id, filters)
        view.message = SimpleNamespace(edit=AsyncMock())
        self.cog.views.add(view)
        return view

    @staticmethod
    def context(arguments: str = ""):
        return SimpleNamespace(
            author=SimpleNamespace(id=42), guild=None, prefix="!tf ",
            send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
        )

    async def test_command_opens_default_panel_in_dm_and_binds_message(self):
        ctx = self.context()
        await self.cog.lunch.callback(self.cog, ctx)
        kwargs = ctx.send.await_args.kwargs
        view = kwargs["view"]
        self.assertIs(view.message, ctx.send.return_value)
        self.assertEqual(view.filters, LunchFilters())
        self.assertEqual(view.author_id, 42)
        self.assertEqual(view.timeout, 180)
        self.assertEqual(view.state, "settings")
        self.assertEqual(len(view.children), 4)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        self.media.wish.assert_not_called()

    async def test_command_arguments_prefill_filters_and_custom_budget(self):
        ctx = self.context()
        await self.cog.lunch.callback(self.cog, ctx, arguments="CHAY 60k")
        view = ctx.send.await_args.kwargs["view"]
        self.assertEqual(view.filters, LunchFilters(60, True))
        selected = [option.value for option in view.budget_select.options if option.default]
        self.assertEqual(selected, ["custom"])
        self.assertEqual(
            [option.value for option in view.diet_select.options if option.default],
            ["chay"],
        )

    async def test_invalid_command_arguments_do_not_open_panel(self):
        for arguments in ("0", "chay CHAY", "50 75", "beef"):
            with self.subTest(arguments=arguments):
                ctx = self.context()
                await self.cog.lunch.callback(self.cog, ctx, arguments=arguments)
                self.assertNotIn("view", ctx.send.await_args.kwargs)
                self.assertIn("!tf lunch", ctx.send.await_args.args[0])
        self.assertFalse(self.cog.views)

    async def test_initial_send_failure_does_not_retain_view(self):
        ctx = self.context()
        ctx.send.side_effect = http_error()
        with self.assertRaises(discord.HTTPException):
            await self.cog.lunch.callback(self.cog, ctx)
        self.assertFalse(self.cog.views)

    async def test_cooldown_has_private_state_independent_friendly_message(self):
        ctx = self.context()
        error = commands.CommandOnCooldown(commands.Cooldown(1, 3), 2.2, commands.BucketType.user)
        await self.cog.lunch_error(ctx, error)
        self.assertIn("3 giây", ctx.send.await_args.args[0])
        self.assertNotIn("view", ctx.send.await_args.kwargs)

    async def test_only_owner_can_change_filters_or_roll(self):
        view = self.view()
        outsider = make_interaction(99)
        await view.set_filters(outsider, LunchFilters(35, True))
        await view.start_roll(make_interaction(99))
        self.assertEqual(view.filters, LunchFilters())
        self.assertFalse(view.rolling)
        outsider.response.send_message.assert_awaited_once()
        self.assertTrue(outsider.response.send_message.await_args.kwargs["ephemeral"])
        self.media.wish.assert_not_called()

    async def test_dropdown_filters_preserve_other_selection(self):
        view = self.view()
        view.budget_select._values = ["50"]
        await view.budget_select.callback(make_interaction())
        view.diet_select._values = ["chay"]
        interaction = make_interaction()
        await view.diet_select.callback(interaction)
        self.assertEqual(view.filters, LunchFilters(50, True))
        self.assertEqual(view.state, "settings")
        embed = interaction.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("**2 món**", embed.fields[1].value)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["attachments"], [])
        self.media.wish.assert_not_called()

    async def test_custom_modal_prefills_and_accepts_budget(self):
        view = self.view(LunchFilters(50, True))
        view.budget_select._values = ["custom"]
        interaction = make_interaction()
        await view.budget_select.callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, BudgetModal)
        self.assertEqual(modal.budget_input.default, "50")
        modal.budget_input._value = "65K"
        await modal.on_submit(make_interaction())
        self.assertEqual(view.filters, LunchFilters(65, True))

    async def test_invalid_modal_budget_preserves_filters(self):
        view = self.view(LunchFilters(50, True))
        modal = BudgetModal(view)
        modal.budget_input._value = "0"
        interaction = make_interaction()
        await modal.on_submit(interaction)
        self.assertEqual(view.filters, LunchFilters(50, True))
        interaction.response.edit_message.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_modal_submission_after_timeout_cannot_change_filters(self):
        view = self.view(LunchFilters(50, True))
        modal = BudgetModal(view)
        modal.budget_input._value = "65k"
        await view.on_timeout()
        interaction = make_interaction()
        await modal.on_submit(interaction)
        self.assertEqual(view.filters, LunchFilters(50, True))
        interaction.response.edit_message.assert_not_awaited()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_budget_and_diet_dropdown_can_remove_each_filter(self):
        view = self.view(LunchFilters(50, True))
        view.budget_select._values = ["all"]
        await view.budget_select.callback(make_interaction())
        self.assertEqual(view.filters, LunchFilters(None, True))
        view.diet_select._values = ["all"]
        await view.diet_select.callback(make_interaction())
        self.assertEqual(view.filters, LunchFilters())
        self.assertEqual(
            [option.value for option in view.budget_select.options if option.default],
            ["all"],
        )

    async def test_failed_filter_edit_restores_previous_selection(self):
        view = self.view()
        interaction = make_interaction()
        interaction.response.edit_message.side_effect = http_error()
        with self.assertRaises(discord.HTTPException):
            await view.set_filters(interaction, LunchFilters(35, True))
        self.assertEqual(view.filters, LunchFilters())
        self.assertFalse(view.roll_button.disabled)

    async def test_empty_matches_disable_roll_and_do_not_start_animation(self):
        view = self.view(LunchFilters(1))
        self.assertTrue(view.roll_button.disabled)
        interaction = make_interaction()
        await view.start_roll(interaction)
        interaction.response.edit_message.assert_not_awaited()
        self.media.wish.assert_not_called()
        self.assertFalse(self.cog.rolling_users)

    async def test_roll_reveals_selected_food_after_gif_and_delay(self):
        view = self.view(LunchFilters(50, True))
        interaction = make_interaction()
        events = []
        initial_acknowledge = interaction.response.defer.side_effect

        async def defer(*args, **kwargs):
            events.append("defer")
            await initial_acknowledge(*args, **kwargs)

        async def delay(seconds):
            events.append("delay")
            self.assertEqual(seconds, 0.3)
            self.assertEqual(view.state, "rolling")

        async def edit(*args, **kwargs):
            self.assertEqual(len(kwargs["attachments"]), 1)
            self.assertFalse(kwargs["allowed_mentions"].users)
            self.assertIsNone(view.current_food)
            if kwargs["embed"].image.url == "attachment://lunch_wish.gif":
                events.append("gif")
                self.assertEqual(kwargs["attachments"][0].filename, "lunch_wish.gif")
                self.assertTrue(all(item.disabled for item in view.children))
                self.assertTrue(interaction.response.is_done())
            else:
                events.append("food")
                self.assertEqual(kwargs["attachments"][0].filename, "lunch_7.png")
                self.assertEqual(kwargs["embed"].image.url, "attachment://lunch_7.png")
                self.assertIn(FOODS[1].name, kwargs["embed"].title)

        interaction.response.defer.side_effect = defer
        interaction.edit_original_response.side_effect = edit
        with patch("cogs.utils.lunch.choose_food", return_value=FOODS[1]) as choose, patch(
            "cogs.utils.lunch.asyncio.sleep", side_effect=delay,
        ):
            await view.start_roll(interaction)
        choose.assert_called_once_with((FOODS[1], FOODS[2]), None)
        self.assertEqual(events, ["defer", "gif", "delay", "food"])
        interaction.response.edit_message.assert_not_awaited()
        self.assertEqual(view.current_food, FOODS[1])
        self.assertEqual(view.state, "result")
        self.assertEqual(view.filters, LunchFilters(50, True))
        self.assertEqual(view.roll_button.label, "Đổi món")
        self.assertFalse(view.roll_button.disabled)
        self.assertFalse(self.cog.rolling_users)
        self.assertFalse(self.cog.roll_tasks)

    async def test_gif_and_result_use_the_selected_food_price_tier(self):
        for price, tier, color, stars in (
            (65, "blue", 0x4B9EFF, "★★★"),
            (66, "purple", 0xA875FF, "★★★★"),
            (131, "gold", 0xE8B84D, "★★★★★"),
        ):
            with self.subTest(price=price):
                food = Food(0, "Món thử", "Mô tả món", price)
                self.cog.foods = (food,)
                self.media.wish.reset_mock()
                view = self.view()
                interaction = make_interaction()
                with patch("cogs.utils.lunch.asyncio.sleep", new_callable=AsyncMock):
                    await view.start_roll(interaction)
                self.media.wish.assert_called_once_with(tier)
                edits = interaction.edit_original_response.await_args_list
                self.assertEqual(len(edits), 2)
                self.assertEqual(edits[0].kwargs["embed"].color.value, color)
                self.assertEqual(edits[1].kwargs["embed"].color.value, color)
                self.assertEqual(edits[1].kwargs["embed"].fields[1].value, stars)
                self.assertEqual(
                    [call.kwargs["attachments"][0].filename for call in edits],
                    ["lunch_wish.gif", "lunch_0.png"],
                )

    async def test_reveal_waits_for_both_animation_and_image_rendering(self):
        view = self.view()
        interaction = make_interaction()
        delay_finished, rendering_started, release_render = (
            asyncio.Event(), asyncio.Event(), asyncio.Event(),
        )

        async def delay(seconds):
            delay_finished.set()

        async def render(function, image_id):
            rendering_started.set()
            await release_render.wait()
            return self.media.food_png.return_value

        with patch("cogs.utils.lunch.asyncio.sleep", side_effect=delay), patch(
            "cogs.utils.lunch.asyncio.to_thread", side_effect=render,
        ):
            task = asyncio.create_task(view.start_roll(interaction))
            try:
                await asyncio.wait_for(delay_finished.wait(), timeout=2)
                await asyncio.wait_for(rendering_started.wait(), timeout=2)
                self.assertIsNone(view.current_food)
                self.assertTrue(view.rolling)
                self.assertEqual(interaction.edit_original_response.await_count, 1)
            finally:
                release_render.set()
                await asyncio.wait_for(task, timeout=2)
        self.assertEqual(interaction.edit_original_response.await_count, 2)
        self.assertIsNotNone(view.current_food)

    async def test_singleton_result_disables_reroll_and_keeps_change_filters(self):
        view = self.view(LunchFilters(35, True))
        with patch("cogs.utils.lunch.asyncio.sleep", new_callable=AsyncMock):
            await view.start_roll(make_interaction())
        self.assertEqual(view.current_food, FOODS[1])
        self.assertTrue(view.roll_button.disabled)
        self.assertFalse(view.filters_button.disabled)
        interaction = make_interaction()
        await view.start_roll(interaction)
        interaction.response.edit_message.assert_not_awaited()

    async def test_reroll_avoids_current_and_change_filters_removes_attachment(self):
        view = self.view(LunchFilters(50, True))
        view.current_food = FOODS[1]
        view.state = "result"
        with patch("cogs.utils.lunch.asyncio.sleep", new_callable=AsyncMock):
            await view.start_roll(make_interaction())
        self.assertEqual(view.current_food, FOODS[2])
        self.assertEqual(view.filters, LunchFilters(50, True))
        interaction = make_interaction()
        await view.filters_button.callback(interaction)
        self.assertEqual(view.state, "settings")
        self.assertEqual(view.filters, LunchFilters(50, True))
        self.assertFalse(view.budget_select.disabled)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["attachments"], [])

    async def test_result_embed_handles_empty_quip_and_shows_estimated_price(self):
        view = self.view()
        with_quip = view.build_result_embed(FOODS[1])
        self.assertIn(FOODS[1].quip, with_quip.description)
        self.assertIn("35.000₫", with_quip.fields[0].value)
        self.assertEqual(view.build_result_embed(FOODS[2]).description, FOODS[2].sub)

    async def test_roll_failure_restores_controls_and_releases_user(self):
        view = self.view()
        self.media.food_png.side_effect = OSError("Corrupt image")
        interaction = make_interaction()
        with patch("cogs.utils.lunch.asyncio.sleep", new_callable=AsyncMock), self.assertLogs(
            "cogs.utils.lunch", level="ERROR",
        ):
            await view.start_roll(interaction)
        self.assertEqual(view.state, "settings")
        self.assertFalse(view.rolling)
        self.assertFalse(view.roll_button.disabled)
        self.assertFalse(self.cog.rolling_users)
        self.assertFalse(self.cog.roll_tasks)
        self.assertIsNone(view.current_food)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        self.assertEqual(interaction.edit_original_response.await_args.kwargs["attachments"], [])

    async def test_failed_result_edit_does_not_commit_new_food(self):
        view = self.view()
        view.current_food = FOODS[0]
        interaction = make_interaction()
        interaction.edit_original_response.side_effect = [None, http_error(), None]
        with patch("cogs.utils.lunch.asyncio.sleep", new_callable=AsyncMock), self.assertLogs(
            "cogs.utils.lunch", level="ERROR",
        ):
            await view.start_roll(interaction)
        self.assertEqual(view.current_food, FOODS[0])
        self.assertEqual(view.state, "settings")
        self.assertFalse(self.cog.rolling_users)

    async def test_failed_initial_gif_edit_recovers_without_rendering_food(self):
        view = self.view()
        interaction = make_interaction()
        interaction.edit_original_response.side_effect = [http_error(), None]
        with self.assertLogs("cogs.utils.lunch", level="ERROR"):
            await view.start_roll(interaction)
        self.media.food_png.assert_not_called()
        self.assertIsNone(view.current_food)
        self.assertEqual(view.state, "settings")
        self.assertFalse(view.roll_button.disabled)
        self.assertFalse(self.cog.rolling_users)
        self.assertEqual(interaction.edit_original_response.await_args.kwargs["attachments"], [])

    async def test_failed_recovery_edit_closes_unusable_panel(self):
        view = self.view()
        interaction = make_interaction()
        interaction.edit_original_response.side_effect = http_error()
        with self.assertLogs("cogs.utils.lunch", level="ERROR"):
            await view.start_roll(interaction)
        self.assertTrue(view.closed)
        self.assertTrue(view.is_finished())
        self.assertNotIn(view, self.cog.views)
        self.assertTrue(all(item.disabled for item in view.children))
        self.assertFalse(self.cog.rolling_users)
        self.assertFalse(self.cog.roll_tasks)

    async def test_failed_change_filters_edit_restores_result_controls(self):
        view = self.view()
        view.state = "result"
        view.current_food = FOODS[0]
        view._refresh_controls()
        interaction = make_interaction()
        interaction.response.edit_message.side_effect = http_error()
        with self.assertRaises(discord.HTTPException):
            await view.filters_button.callback(interaction)
        self.assertEqual(view.state, "result")
        self.assertEqual(view.current_food, FOODS[0])
        self.assertTrue(view.budget_select.disabled)
        self.assertFalse(view.filters_button.disabled)

    async def test_timeout_disables_panel_and_ignores_missing_message(self):
        view = self.view()
        view.message.edit.side_effect = http_error()
        await view.on_timeout()
        self.assertTrue(view.closed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        self.assertNotIn(view, self.cog.views)
        interaction = make_interaction()
        self.assertFalse(await view.interaction_check(interaction))
        view.message.edit.assert_awaited_once()

    async def test_running_roll_rejects_other_panel_and_filter_changes(self):
        view = self.view()
        second_view = self.view()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delay(seconds):
            entered.set()
            await release.wait()

        with patch("cogs.utils.lunch.asyncio.sleep", side_effect=delay):
            task = asyncio.create_task(view.start_roll(make_interaction()))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                second = make_interaction()
                await second_view.start_roll(second)
                second.response.defer.assert_not_awaited()
                second.edit_original_response.assert_not_awaited()
                self.assertTrue(second.response.send_message.await_args.kwargs["ephemeral"])
                overlapping = make_interaction()
                await view.start_roll(overlapping)
                overlapping.response.defer.assert_not_awaited()
                overlapping.edit_original_response.assert_not_awaited()
                self.assertTrue(overlapping.response.send_message.await_args.kwargs["ephemeral"])
                await view.set_filters(make_interaction(), LunchFilters(35, True))
                self.assertEqual(view.filters, LunchFilters())
                self.assertEqual(self.cog.rolling_users, {42})
            finally:
                release.set()
                await asyncio.wait_for(task, timeout=2)

    async def test_timeout_during_animation_cancels_reveal_and_releases_guard(self):
        view = self.view()
        interaction = make_interaction()
        entered = asyncio.Event()

        async def delay(seconds):
            entered.set()
            await asyncio.Event().wait()

        with patch("cogs.utils.lunch.asyncio.sleep", side_effect=delay):
            task = asyncio.create_task(view.start_roll(interaction))
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(view.on_timeout(), timeout=2)
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(view.closed)
        self.assertFalse(self.cog.rolling_users)
        self.assertFalse(self.cog.roll_tasks)
        interaction.edit_original_response.assert_awaited_once()
        self.assertEqual(
            interaction.edit_original_response.await_args.kwargs["embed"].image.url,
            "attachment://lunch_wish.gif",
        )
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_cog_unload_cancels_rolls_and_disables_views(self):
        view = self.view()
        interaction = make_interaction()
        entered = asyncio.Event()

        async def delay(seconds):
            entered.set()
            await asyncio.Event().wait()

        with patch("cogs.utils.lunch.asyncio.sleep", side_effect=delay):
            task = asyncio.create_task(view.start_roll(interaction))
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(self.cog.cog_unload(), timeout=2)
        self.assertTrue(task.cancelled())
        self.assertTrue(view.closed)
        self.assertFalse(self.cog.rolling_users)
        self.assertFalse(self.cog.views)
        self.media.clear_cache.assert_called_once()
        interaction.edit_original_response.assert_awaited_once()
        self.assertEqual(
            interaction.edit_original_response.await_args.kwargs["embed"].image.url,
            "attachment://lunch_wish.gif",
        )

    async def test_unload_disables_message_after_in_flight_filter_edit(self):
        view = self.view()
        interaction = make_interaction()
        entered, release = asyncio.Event(), asyncio.Event()
        events = []

        async def edit_filter(**kwargs):
            entered.set()
            await release.wait()
            events.append("filter")

        async def disable(**kwargs):
            events.append("disabled")
            self.assertTrue(all(item.disabled for item in kwargs["view"].children))

        interaction.response.edit_message.side_effect = edit_filter
        view.message.edit.side_effect = disable
        editing = asyncio.create_task(view.set_filters(interaction, LunchFilters(50, True)))
        await asyncio.wait_for(entered.wait(), timeout=2)
        unloading = asyncio.create_task(self.cog.cog_unload())
        # Let unload close the panel while the earlier Discord edit is suspended.
        await asyncio.sleep(0)
        self.assertTrue(view.closed)
        view.message.edit.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.gather(editing, unloading), timeout=2)
        self.assertEqual(events, ["filter", "disabled"])


if __name__ == "__main__":
    unittest.main()
