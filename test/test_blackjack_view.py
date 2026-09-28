import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from cogs.minigames._playing_cards import Card
from cogs.minigames.blackjack._blackjack_helpers import BlackjackOutcome
from cogs.minigames.blackjack.blackjack import (
    BLACKJACK_TABLE_FILENAME,
    BLACKJACK_TIMEOUT_SECONDS,
    NO_MENTIONS,
    BlackjackBetModal,
    BlackjackView,
)


PLAYER_HAND = [Card(10, "♠"), Card(7, "♥")]
DEALER_HAND = [Card(9, "♦"), Card(8, "♣")]


class FakeBlackjackGame:
    def __init__(self) -> None:
        self.player_hand = list(PLAYER_HAND)
        self.dealer_hand = list(DEALER_HAND)
        self.finished = False
        self.outcome = None

    def hit(self) -> Card:
        drawn = Card(4, "♦")
        self.player_hand.append(drawn)
        return drawn

    def stand(self) -> BlackjackOutcome:
        self.finished = True
        self.outcome = BlackjackOutcome.PLAYER_WIN
        return self.outcome


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args, **kwargs):
        acknowledged["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
            send_modal=AsyncMock(side_effect=acknowledge),
            is_done=lambda: acknowledged["done"],
        ),
    )


def make_view() -> tuple[BlackjackView, SimpleNamespace]:
    bank = SimpleNamespace(
        credit=MagicMock(return_value=105),
        reserve_wager=MagicMock(return_value=90),
    )
    cog = SimpleNamespace(
        bank=bank,
        unregister=MagicMock(),
        deal_hand=MagicMock(return_value=(FakeBlackjackGame(), 90)),
    )
    view = BlackjackView(
        cog,
        game=FakeBlackjackGame(),
        user_id=42,
        guild_id=None,
        display_name="Người chơi",
        bet=5,
        balance_after_wager=95,
        session_id="blackjack-session",
    )
    return view, cog


class TestBlackjackView(unittest.IsolatedAsyncioTestCase):
    async def test_table_is_owner_only_and_hides_dealer(self) -> None:
        view, _ = make_view()
        owner = make_interaction()
        stranger = make_interaction(99)

        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        self.assertEqual(view.timeout, BLACKJACK_TIMEOUT_SECONDS)
        self.assertTrue(view.play_again_button.disabled)
        self.assertTrue(view.bet_button.disabled)
        self.assertFalse(view.hit_button.disabled)
        fields = {field.name: field.value for field in view.build_embed().fields}
        self.assertIn("🂠", fields["Bài nhà cái"])
        png = view._render_table()
        self.assertTrue(png.startswith(b"\x89PNG"))
        view.stop()

    async def test_stand_settles_win_and_unlocks_replay(self) -> None:
        view, cog = make_view()
        interaction = make_interaction()

        await view.stand_button.callback(interaction)

        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "blackjack",
            10,
            "blackjack-session",
            "win",
        )
        self.assertTrue(view._settled)
        self.assertFalse(view._closed)
        self.assertTrue(view.hit_button.disabled)
        self.assertTrue(view.stand_button.disabled)
        self.assertFalse(view.play_again_button.disabled)
        self.assertFalse(view.bet_button.disabled)
        cog.unregister.assert_not_called()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
        self.assertEqual(len(kwargs["attachments"]), 1)
        self.assertEqual(
            kwargs["embed"].image.url,
            f"attachment://{BLACKJACK_TABLE_FILENAME}",
        )
        self.assertIn("Chơi lại", kwargs["embed"].description)
        fields = {field.name: field.value for field in kwargs["embed"].fields}
        self.assertNotIn("🂠", fields["Bài nhà cái"])

        repeated = make_interaction()
        await view.hit_button.callback(repeated)
        cog.bank.credit.assert_called_once()
        repeated.response.send_message.assert_awaited_once()

    async def test_play_again_deals_a_new_hand_with_the_current_bet(self) -> None:
        view, cog = make_view()
        await view.stand_button.callback(make_interaction())
        interaction = make_interaction()

        await view.play_again_button.callback(interaction)

        cog.deal_hand.assert_called_once_with(
            user_id=42,
            guild_id=None,
            bet=5,
            session_id=view.session_id,
        )
        self.assertFalse(view._settled)
        self.assertFalse(view.game.finished)
        self.assertFalse(view.hit_button.disabled)
        self.assertTrue(view.play_again_button.disabled)
        self.assertEqual(view.balance_after, 90)
        interaction.response.edit_message.assert_awaited()

    async def test_play_again_reports_insufficient_balance(self) -> None:
        view, cog = make_view()
        await view.stand_button.callback(make_interaction())
        cog.deal_hand.return_value = None
        interaction = make_interaction()

        await view.play_again_button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        self.assertTrue(view._settled)
        self.assertFalse(view.play_again_button.disabled)

    async def test_bet_modal_updates_next_wager_after_a_hand(self) -> None:
        view, _ = make_view()
        await view.stand_button.callback(make_interaction())
        open_modal = make_interaction()
        await view.bet_button.callback(open_modal)
        open_modal.response.send_modal.assert_awaited_once()
        modal = open_modal.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, BlackjackBetModal)
        self.assertEqual(modal.amount.default, "5")

        modal.amount._value = "1,000"
        submit = make_interaction()
        await modal.on_submit(submit)
        self.assertEqual(view.bet, 1000)
        submit.response.edit_message.assert_awaited_once()
        description = submit.response.edit_message.await_args.kwargs["embed"].description
        self.assertIn("1,000 TC", description)

    async def test_bet_cannot_change_during_an_open_hand(self) -> None:
        view, _ = make_view()
        interaction = make_interaction()
        await view.bet_button.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.send_modal.assert_not_awaited()
        self.assertEqual(view.bet, 5)

    async def test_timeout_refunds_an_open_hand_once(self) -> None:
        view, cog = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()
        await view.on_timeout()

        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "blackjack",
            5,
            "blackjack-session",
            "refund",
        )
        self.assertTrue(view._closed)
        self.assertTrue(all(child.disabled for child in view.children))
        cog.unregister.assert_called_once_with(view)
        view.message.edit.assert_awaited_once()
        self.assertIs(
            view.message.edit.await_args.kwargs["allowed_mentions"],
            NO_MENTIONS,
        )

    async def test_timeout_after_settlement_does_not_refund(self) -> None:
        view, cog = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.stand_button.callback(make_interaction())
        cog.bank.credit.reset_mock()
        cog.unregister.reset_mock()

        await view.on_timeout()

        cog.bank.credit.assert_not_called()
        self.assertTrue(view._closed)
        cog.unregister.assert_called_once_with(view)
        view.message.edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
