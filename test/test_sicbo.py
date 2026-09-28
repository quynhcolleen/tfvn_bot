import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.minigames.sicbo._sicbo_helpers import (
    SicBoBet,
    format_dice,
    payout_return,
    roll_dice,
    winning_bet,
)
from cogs.minigames.sicbo.sicbo import (
    NO_MENTIONS,
    SICBO_TABLE_FILENAME,
    SicBoBetModal,
    SicBoView,
)


class TestSicBoHelpers(unittest.TestCase):
    def test_markets_and_payouts(self) -> None:
        self.assertIs(winning_bet((1, 2, 3)), SicBoBet.SMALL)
        self.assertIs(winning_bet((6, 5, 4)), SicBoBet.BIG)
        self.assertIs(winning_bet((4, 4, 4)), SicBoBet.TRIPLE)
        self.assertEqual(payout_return(5, SicBoBet.SMALL, (1, 2, 3)), 10)
        self.assertEqual(payout_return(5, SicBoBet.BIG, (6, 5, 4)), 10)
        self.assertEqual(payout_return(5, SicBoBet.TRIPLE, (4, 4, 4)), 155)
        self.assertEqual(payout_return(5, SicBoBet.BIG, (4, 4, 4)), 0)
        self.assertEqual(payout_return(5, SicBoBet.SMALL, (6, 5, 4)), 0)
        self.assertEqual(format_dice((6, 5, 4)), "6 | 5 | 4")

    def test_invalid_dice_and_bets_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            winning_bet((1, 2))
        with self.assertRaises(ValueError):
            winning_bet((1, 2, 7))
        with self.assertRaises(ValueError):
            payout_return(0, SicBoBet.BIG, (6, 5, 4))

    def test_roll_is_three_legal_faces(self) -> None:
        dice = roll_dice()
        self.assertEqual(len(dice), 3)
        self.assertTrue(all(1 <= value <= 6 for value in dice))


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
    )


def make_view() -> tuple[SicBoView, SimpleNamespace]:
    bank = SimpleNamespace(credit=MagicMock(return_value=105))
    cog = SimpleNamespace(
        bank=bank,
        unregister=MagicMock(),
        reserve_round=MagicMock(return_value=90),
    )
    view = SicBoView(
        cog,
        user_id=42,
        guild_id=None,
        display_name="Người chơi",
        bet=5,
        balance_after_wager=95,
        session_id="sicbo-session",
    )
    return view, cog


class TestSicBoView(unittest.IsolatedAsyncioTestCase):
    async def test_board_is_owner_only(self) -> None:
        view, _ = make_view()
        self.assertTrue(await view.interaction_check(make_interaction()))
        stranger = make_interaction(99)
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        self.assertTrue(view.play_again_button.disabled)
        self.assertTrue(view.bet_button.disabled)
        self.assertFalse(view.big_button.disabled)
        png = view._render_table()
        self.assertTrue(png.startswith(b"\x89PNG"))
        view.stop()

    async def test_failed_settlement_retries_the_same_roll(self) -> None:
        view, cog = make_view()
        with patch(
            "cogs.minigames.sicbo.sicbo.roll_dice",
            side_effect=[(6, 5, 4), (1, 1, 1)],
        ):
            view._settle_roll(SicBoBet.BIG)
            view._settled = False
            view._closed = False
            view._settle_roll(SicBoBet.SMALL)

        self.assertEqual(view.dice, (6, 5, 4))
        self.assertIs(view.choice, SicBoBet.BIG)
        self.assertEqual(cog.bank.credit.call_count, 2)
        view.stop()

    async def test_choice_settles_and_unlocks_replay(self) -> None:
        view, cog = make_view()
        interaction = make_interaction()
        with patch("cogs.minigames.sicbo.sicbo.roll_dice", return_value=(4, 4, 4)):
            await view.triple_button.callback(interaction)

        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "sicbo",
            155,
            "sicbo-session",
            "win",
        )
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(
            kwargs["embed"].image.url,
            f"attachment://{SICBO_TABLE_FILENAME}",
        )
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
        self.assertIn("Chơi lại", kwargs["embed"].description)
        self.assertTrue(view._settled)
        self.assertFalse(view._closed)
        self.assertTrue(view.big_button.disabled)
        self.assertFalse(view.play_again_button.disabled)
        self.assertFalse(view.bet_button.disabled)
        cog.unregister.assert_not_called()

    async def test_play_again_reserves_a_new_round(self) -> None:
        view, cog = make_view()
        with patch("cogs.minigames.sicbo.sicbo.roll_dice", return_value=(6, 5, 4)):
            await view.big_button.callback(make_interaction())
        interaction = make_interaction()

        await view.play_again_button.callback(interaction)

        cog.reserve_round.assert_called_once_with(
            user_id=42,
            guild_id=None,
            bet=5,
            session_id=view.session_id,
        )
        self.assertIsNone(view.choice)
        self.assertIsNone(view.dice)
        self.assertFalse(view._settled)
        self.assertFalse(view.big_button.disabled)
        self.assertTrue(view.play_again_button.disabled)
        self.assertEqual(view.balance_after, 90)
        interaction.response.edit_message.assert_awaited()

    async def test_bet_modal_updates_next_wager_after_a_round(self) -> None:
        view, _ = make_view()
        with patch("cogs.minigames.sicbo.sicbo.roll_dice", return_value=(6, 5, 4)):
            await view.big_button.callback(make_interaction())
        open_modal = make_interaction()
        await view.bet_button.callback(open_modal)
        open_modal.response.send_modal.assert_awaited_once()
        modal = open_modal.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, SicBoBetModal)
        self.assertEqual(modal.amount.default, "5")

        modal.amount._value = "1,000"
        submit = make_interaction()
        await modal.on_submit(submit)
        self.assertEqual(view.bet, 1000)
        self.assertEqual(view.last_bet, 5)
        description = submit.response.edit_message.await_args.kwargs["embed"].description
        self.assertIn("1,000 TC", description)

    async def test_bet_cannot_change_during_an_open_round(self) -> None:
        view, _ = make_view()
        interaction = make_interaction()
        await view.bet_button.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.send_modal.assert_not_awaited()
        self.assertEqual(view.bet, 5)

    async def test_timeout_refunds_an_open_round_once(self) -> None:
        view, cog = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        await view.on_timeout()
        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "sicbo",
            5,
            "sicbo-session",
            "refund",
        )
        self.assertTrue(view._closed)
        view.message.edit.assert_awaited_once()

    async def test_timeout_after_settlement_does_not_refund(self) -> None:
        view, cog = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())
        with patch("cogs.minigames.sicbo.sicbo.roll_dice", return_value=(6, 5, 4)):
            await view.big_button.callback(make_interaction())
        cog.bank.credit.reset_mock()
        cog.unregister.reset_mock()

        await view.on_timeout()

        cog.bank.credit.assert_not_called()
        self.assertTrue(view._closed)
        cog.unregister.assert_called_once_with(view)
        view.message.edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
