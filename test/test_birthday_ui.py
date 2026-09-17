import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext.commands.view import StringView

from cogs.funny_things.birthday._birthday_ui import (
    BIRTHDAY_UI_TIMEOUT_SECONDS,
    BirthdayView,
    days_in_month,
    is_valid_birthday,
)
from cogs.funny_things.birthday.birthday import BirthdayCog


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            is_done=lambda: False,
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


async def choose_birthday(
    view: BirthdayView,
    *,
    month: int,
    day: int,
) -> None:
    view.month_select._values = [str(month)]
    await view.month_select.callback(make_interaction())
    if day > 15:
        await view.day_page_button.callback(make_interaction())
    view.day_select._values = [str(day)]
    await view.day_select.callback(make_interaction())


class TestBirthdayDateHelpers(unittest.TestCase):
    def test_month_lengths_use_birthday_maximums(self) -> None:
        expected = {
            1: 31,
            2: 29,
            3: 31,
            4: 30,
            5: 31,
            6: 30,
            7: 31,
            8: 31,
            9: 30,
            10: 31,
            11: 30,
            12: 31,
        }

        self.assertEqual(
            {month: days_in_month(month) for month in range(1, 13)},
            expected,
        )

    def test_leap_day_is_allowed_but_impossible_dates_are_rejected(self) -> None:
        self.assertTrue(is_valid_birthday(2, 29))
        self.assertTrue(is_valid_birthday(4, 30))

        for month, day in ((2, 30), (4, 31), (1, 0), (0, 1), (13, 1)):
            with self.subTest(month=month, day=day):
                self.assertFalse(is_valid_birthday(month, day))


class TestBirthdayView(unittest.IsolatedAsyncioTestCase):
    async def test_valid_existing_birthday_is_prefilled(self) -> None:
        view = BirthdayView(
            author_id=42,
            save_callback=AsyncMock(),
            initial_month=2,
            initial_day=29,
        )
        self.addAsyncCleanup(self._stop_view, view)

        self.assertEqual(view.timeout, BIRTHDAY_UI_TIMEOUT_SECONDS)
        self.assertEqual(BIRTHDAY_UI_TIMEOUT_SECONDS, 180)
        self.assertEqual(view.month, 2)
        self.assertEqual(view.day, 29)
        self.assertEqual(view.day_page, 1)
        self.assertFalse(view.day_select.disabled)
        self.assertFalse(view.day_page_button.disabled)
        self.assertFalse(hasattr(view, "range_select"))
        self.assertEqual(
            [item.row for item in view.children],
            [0, 1, 2, 2, 2],
        )
        self.assertEqual(
            len(
                [
                    item
                    for item in view.children
                    if isinstance(item, discord.ui.Select)
                ]
            ),
            2,
        )
        self.assertTrue(
            next(
                option for option in view.month_select.options
                if option.value == "2"
            ).default
        )
        self.assertEqual(
            [int(option.value) for option in view.day_select.options],
            list(range(16, 30)),
        )
        self.assertEqual(view.day_select.placeholder, "Chọn ngày sinh (16–29)")
        self.assertEqual(view.day_page_button.label, "Xem ngày 1–15")
        self.assertTrue(
            next(
                option for option in view.day_select.options
                if option.value == "29"
            ).default
        )
        self.assertIn("29/2", str(view.build_embed().to_dict()))

    async def test_month_changes_preserve_valid_day_or_reset_invalid_day(
        self,
    ) -> None:
        view = BirthdayView(
            author_id=42,
            save_callback=AsyncMock(),
            initial_month=1,
            initial_day=31,
        )
        self.addAsyncCleanup(self._stop_view, view)

        retained_interaction = make_interaction()
        view.month_select._values = ["3"]
        await view.month_select.callback(retained_interaction)

        self.assertEqual(view.month, 3)
        self.assertEqual(view.day, 31)
        self.assertEqual(view.day_page, 1)
        self.assertTrue(
            next(
                option for option in view.day_select.options
                if option.value == "31"
            ).default
        )
        retained_interaction.response.edit_message.assert_awaited_once()

        cleared_interaction = make_interaction()
        view.month_select._values = ["4"]
        await view.month_select.callback(cleared_interaction)

        self.assertEqual(view.month, 4)
        self.assertIsNone(view.day)
        self.assertEqual(view.day_page, 0)
        self.assertEqual(
            [int(option.value) for option in view.day_select.options],
            list(range(1, 16)),
        )
        self.assertEqual(view.day_select.placeholder, "Chọn ngày sinh (1–15)")
        self.assertEqual(view.day_page_button.label, "Xem ngày 16–30")
        self.assertFalse(view.day_select.disabled)
        self.assertTrue(view.confirm_button.disabled)
        cleared_interaction.response.edit_message.assert_awaited_once()

    async def test_page_toggle_switches_day_options_and_clears_selection(
        self,
    ) -> None:
        view = BirthdayView(author_id=42, save_callback=AsyncMock())
        self.addAsyncCleanup(self._stop_view, view)
        view.month_select._values = ["4"]
        await view.month_select.callback(make_interaction())
        view.day_select._values = ["7"]
        await view.day_select.callback(make_interaction())
        self.assertEqual(view.day, 7)

        next_page = make_interaction()
        await view.day_page_button.callback(next_page)

        self.assertEqual(view.day_page, 1)
        self.assertIsNone(view.day)
        self.assertEqual(
            [int(option.value) for option in view.day_select.options],
            list(range(16, 31)),
        )
        self.assertEqual(view.day_select.placeholder, "Chọn ngày sinh (16–30)")
        self.assertEqual(view.day_page_button.label, "Xem ngày 1–15")
        self.assertTrue(view.confirm_button.disabled)
        next_page.response.edit_message.assert_awaited_once()

        previous_page = make_interaction()
        await view.day_page_button.callback(previous_page)

        self.assertEqual(view.day_page, 0)
        self.assertEqual(
            [int(option.value) for option in view.day_select.options],
            list(range(1, 16)),
        )
        self.assertEqual(view.day_select.placeholder, "Chọn ngày sinh (1–15)")
        self.assertEqual(view.day_page_button.label, "Xem ngày 16–30")
        previous_page.response.edit_message.assert_awaited_once()

    async def test_day_pages_are_inclusive_and_fit_discord_option_limits(
        self,
    ) -> None:
        view = BirthdayView(author_id=42, save_callback=AsyncMock())
        self.addAsyncCleanup(self._stop_view, view)

        self.assertEqual(len(view.month_select.options), 12)
        self.assertLessEqual(len(view.month_select.options), 25)
        for month in range(1, 13):
            view.month = month
            view.day = None
            expected_pages = (
                (0, range(1, 16)),
                (1, range(16, days_in_month(month) + 1)),
            )
            for page, expected_days in expected_pages:
                view.day_page = page
                view._sync_components()
                values = [
                    int(option.value) for option in view.day_select.options
                ]
                self.assertEqual(values, list(expected_days))
                self.assertLessEqual(len(values), 25)

    async def test_tampered_component_values_are_rejected_privately(self) -> None:
        cases = (
            ("month_select", "13"),
            ("month_select", "not-a-month"),
            ("day_select", "1"),
        )
        for component_name, value in cases:
            with self.subTest(component=component_name, value=value):
                view = BirthdayView(author_id=42, save_callback=AsyncMock())
                interaction = make_interaction()
                component = getattr(view, component_name)
                component._values = [value]

                await component.callback(interaction)

                interaction.response.send_message.assert_awaited_once()
                self.assertTrue(
                    interaction.response.send_message.await_args.kwargs[
                        "ephemeral"
                    ]
                )
                interaction.response.edit_message.assert_not_awaited()
                view.stop()

        view = BirthdayView(author_id=42, save_callback=AsyncMock())
        self.addAsyncCleanup(self._stop_view, view)
        view.month_select._values = ["2"]
        await view.month_select.callback(make_interaction())

        wrong_page = make_interaction()
        view.day_select._values = ["16"]
        await view.day_select.callback(wrong_page)
        wrong_page.response.send_message.assert_awaited_once()
        self.assertTrue(
            wrong_page.response.send_message.await_args.kwargs["ephemeral"]
        )

        await view.day_page_button.callback(make_interaction())
        for invalid_day in ("15", "30", "twenty-nine"):
            with self.subTest(day=invalid_day):
                interaction = make_interaction()
                view.day_select._values = [invalid_day]
                await view.day_select.callback(interaction)
                interaction.response.send_message.assert_awaited_once()
                self.assertTrue(
                    interaction.response.send_message.await_args.kwargs[
                        "ephemeral"
                    ]
                )
                interaction.response.edit_message.assert_not_awaited()
        self.assertIsNone(view.day)

        no_month = BirthdayView(author_id=42, save_callback=AsyncMock())
        self.addAsyncCleanup(self._stop_view, no_month)
        page_interaction = make_interaction()
        await no_month.day_page_button.callback(page_interaction)
        page_interaction.response.send_message.assert_awaited_once()
        self.assertTrue(
            page_interaction.response.send_message.await_args.kwargs[
                "ephemeral"
            ]
        )

    async def test_only_invoking_user_can_interact(self) -> None:
        view = BirthdayView(author_id=42, save_callback=AsyncMock())
        self.addAsyncCleanup(self._stop_view, view)

        owner = make_interaction(42)
        stranger = make_interaction(99)
        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        owner.response.send_message.assert_not_awaited()
        stranger.response.send_message.assert_awaited_once()
        self.assertTrue(
            stranger.response.send_message.await_args.kwargs["ephemeral"]
        )

    async def test_confirm_saves_exact_date_and_completes_once(self) -> None:
        save_callback = AsyncMock()
        view = BirthdayView(author_id=42, save_callback=save_callback)
        await choose_birthday(
            view,
            month=6,
            day=15,
        )
        interaction = make_interaction()

        await view.confirm_button.callback(interaction)

        save_callback.assert_awaited_once_with(6, 15)
        interaction.response.defer.assert_awaited_once_with()
        interaction.edit_original_response.assert_awaited_once()
        self.assertTrue(view.completed)
        self.assertFalse(view.submitting)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))

        duplicate = make_interaction()
        await view.confirm_button.callback(duplicate)
        save_callback.assert_awaited_once_with(6, 15)
        duplicate.response.send_message.assert_awaited_once()
        self.assertTrue(
            duplicate.response.send_message.await_args.kwargs["ephemeral"]
        )

    async def test_concurrent_confirmation_cannot_submit_twice(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def save_callback(month: int, day: int) -> None:
            self.assertEqual((month, day), (12, 31))
            started.set()
            await release.wait()

        callback = AsyncMock(side_effect=save_callback)
        view = BirthdayView(author_id=42, save_callback=callback)
        await choose_birthday(
            view,
            month=12,
            day=31,
        )
        first = make_interaction()
        first_task = asyncio.create_task(view.confirm_button.callback(first))
        await started.wait()

        duplicate = make_interaction()
        await view.confirm_button.callback(duplicate)
        duplicate.response.send_message.assert_awaited_once()
        self.assertTrue(
            duplicate.response.send_message.await_args.kwargs["ephemeral"]
        )
        callback.assert_awaited_once_with(12, 31)

        release.set()
        await first_task
        callback.assert_awaited_once_with(12, 31)
        self.assertTrue(view.completed)

    async def test_database_failure_leaves_form_retryable(self) -> None:
        save_callback = AsyncMock(side_effect=[RuntimeError("db down"), None])
        view = BirthdayView(author_id=42, save_callback=save_callback)
        self.addAsyncCleanup(self._stop_view, view)
        await choose_birthday(
            view,
            month=2,
            day=29,
        )

        failed = make_interaction()
        with self.assertLogs(
            "cogs.funny_things.birthday._birthday_ui",
            level="ERROR",
        ):
            await view.confirm_button.callback(failed)

        save_callback.assert_awaited_once_with(2, 29)
        failed.response.defer.assert_awaited_once_with()
        failed.followup.send.assert_awaited_once()
        self.assertTrue(failed.followup.send.await_args.kwargs["ephemeral"])
        self.assertFalse(view.completed)
        self.assertFalse(view.submitting)
        self.assertFalse(view.is_finished())
        self.assertFalse(any(item.disabled for item in view.children))
        self.assertEqual((view.month, view.day_page, view.day), (2, 1, 29))

        retried = make_interaction()
        await view.confirm_button.callback(retried)

        self.assertEqual(save_callback.await_count, 2)
        self.assertEqual(save_callback.await_args_list[1].args, (2, 29))
        self.assertTrue(view.completed)
        self.assertTrue(view.is_finished())

    async def test_cancel_and_timeout_disable_the_form(self) -> None:
        save_callback = AsyncMock()
        cancelled = BirthdayView(author_id=42, save_callback=save_callback)
        cancel_interaction = make_interaction()

        await cancelled.cancel_button.callback(cancel_interaction)

        save_callback.assert_not_awaited()
        self.assertTrue(cancelled.completed)
        self.assertTrue(cancelled.is_finished())
        self.assertTrue(all(item.disabled for item in cancelled.children))
        cancel_interaction.response.edit_message.assert_awaited_once()

        expired = BirthdayView(author_id=42, save_callback=AsyncMock())
        expired.message = SimpleNamespace(edit=AsyncMock())
        await expired.on_timeout()

        self.assertTrue(expired.is_finished())
        self.assertTrue(all(item.disabled for item in expired.children))
        expired.message.edit.assert_awaited_once_with(view=expired)

    async def _stop_view(self, view: BirthdayView) -> None:
        view.stop()


class TestBirthdayCommandIntegration(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, birthdays: MagicMock) -> BirthdayCog:
        cog = object.__new__(BirthdayCog)
        cog.bot = SimpleNamespace()
        cog.db = {"birthdays": birthdays}
        return cog

    def make_context(self) -> SimpleNamespace:
        return SimpleNamespace(
            author=SimpleNamespace(id=42),
            reply=AsyncMock(
                return_value=SimpleNamespace(id=123, edit=AsyncMock())
            ),
            send=AsyncMock(),
        )

    async def test_root_command_opens_prefilled_picker(self) -> None:
        birthdays = MagicMock()
        birthdays.find_one.return_value = {
            "user_id": 42,
            "month": 2,
            "day": 29,
        }
        cog = self.make_cog(birthdays)
        ctx = self.make_context()

        await cog.birthday.callback(cog, ctx)

        birthdays.find_one.assert_called_once_with({"user_id": 42})
        ctx.reply.assert_awaited_once()
        ctx.send.assert_not_awaited()
        kwargs = ctx.reply.await_args.kwargs
        self.assertIsInstance(kwargs["embed"], discord.Embed)
        self.assertIsInstance(kwargs["view"], BirthdayView)
        view = kwargs["view"]
        self.assertEqual((view.month, view.day), (2, 29))
        self.assertIs(view.message, ctx.reply.return_value)
        self.assertFalse(kwargs["mention_author"])
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)
        view.stop()

    async def test_root_picker_confirmation_uses_existing_upsert_shape(self) -> None:
        birthdays = MagicMock()
        birthdays.find_one.return_value = None
        cog = self.make_cog(birthdays)
        ctx = self.make_context()
        await cog.birthday.callback(cog, ctx)
        view = ctx.reply.await_args.kwargs["view"]
        await choose_birthday(
            view,
            month=4,
            day=30,
        )

        await view.confirm_button.callback(make_interaction())

        birthdays.update_one.assert_called_once_with(
            {"user_id": 42},
            {"$set": {"month": 4, "day": 30}},
            upsert=True,
        )

    async def test_legacy_set_accepts_february_29_with_exact_upsert(self) -> None:
        birthdays = MagicMock()
        cog = self.make_cog(birthdays)
        ctx = self.make_context()

        await cog.set_birthday.callback(cog, ctx, 29, 2)

        birthdays.update_one.assert_called_once_with(
            {"user_id": 42},
            {"$set": {"month": 2, "day": 29}},
            upsert=True,
        )
        ctx.send.assert_awaited_once()
        self.assertIn("29/2", ctx.send.await_args.args[0])
        ctx.reply.assert_not_awaited()

    async def test_legacy_set_rejects_real_calendar_overflows(self) -> None:
        invalid_dates = ((31, 4), (30, 2), (0, 1), (1, 13))
        for day, month in invalid_dates:
            with self.subTest(day=day, month=month):
                birthdays = MagicMock()
                cog = self.make_cog(birthdays)
                ctx = self.make_context()

                await cog.set_birthday.callback(cog, ctx, day, month)

                birthdays.update_one.assert_not_called()
                ctx.send.assert_awaited_once()
                ctx.reply.assert_not_awaited()

    async def test_group_routes_legacy_subcommand_without_running_picker(
        self,
    ) -> None:
        self.assertTrue(BirthdayCog.birthday.invoke_without_command)
        ctx = SimpleNamespace(
            view=StringView("set 29 2"),
            invoked_parents=[],
            invoked_with="birthday",
        )
        set_invoke = AsyncMock()

        with patch.object(BirthdayCog.set_birthday, "invoke", set_invoke):
            await BirthdayCog.birthday.invoke(ctx)

        set_invoke.assert_awaited_once_with(ctx)
        self.assertIs(ctx.invoked_subcommand, BirthdayCog.set_birthday)


if __name__ == "__main__":
    unittest.main()
