import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from cogs.minigames._playing_cards import Card, format_card
from cogs.minigames.poker.poker import (
    HIDDEN_DEALER_HAND,
    NO_MENTIONS,
    POKER_TIMEOUT_SECONDS,
    PokerBetModal,
    PokerView,
)


PLAYER_HAND = [
    Card(10, "♠"),
    Card(11, "♠"),
    Card(12, "♠"),
    Card(13, "♠"),
    Card(14, "♠"),
]
DEALER_HAND = [
    Card(2, "♥"),
    Card(4, "♦"),
    Card(6, "♣"),
    Card(8, "♥"),
    Card(9, "♦"),
]


class FakePokerGame:
    def __init__(self, result: int = 1) -> None:
        self.player_hand = list(PLAYER_HAND)
        self.dealer_hand = list(DEALER_HAND)
        self.finished = False
        self.result = None
        self.final_result = result
        self.drawn_indices: tuple[int, ...] | None = None

    def draw(self, indices) -> None:
        self.drawn_indices = tuple(indices)
        self.finished = True
        self.result = self.final_result

    def stand(self) -> None:
        self.drawn_indices = ()
        self.finished = True
        self.result = self.final_result


def make_interaction(user_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
    )


def make_view(*, result: int = 1) -> tuple[PokerView, SimpleNamespace]:
    bank = SimpleNamespace(credit=MagicMock(return_value=105))
    cog = SimpleNamespace(
        bank=bank,
        unregister_game=MagicMock(),
        deal_hand=MagicMock(return_value=(FakePokerGame(result), 90)),
    )
    view = PokerView(
        cog,
        author_id=42,
        owner_name="Người chơi",
        guild_id=None,
        bet=5,
        session_id="poker-session",
        game=FakePokerGame(result),
        balance_after_reserve=95,
    )
    return view, cog


class TestPokerView(unittest.IsolatedAsyncioTestCase):
    async def test_table_is_owner_only_and_hides_dealer(self) -> None:
        view, _ = make_view()
        owner = make_interaction()
        stranger = make_interaction(99)

        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        denial_kwargs = stranger.response.send_message.await_args.kwargs
        self.assertTrue(denial_kwargs["ephemeral"])
        self.assertIs(denial_kwargs["allowed_mentions"], NO_MENTIONS)

        self.assertEqual(view.timeout, POKER_TIMEOUT_SECONDS)
        self.assertEqual(
            [button.label for button in view.card_buttons],
            [format_card(card) for card in PLAYER_HAND],
        )
        self.assertEqual(len(view.children), 9)
        self.assertTrue(view.draw_button.disabled)
        self.assertTrue(view.play_again_button.disabled)
        self.assertTrue(view.bet_button.disabled)
        fields = {field.name: field.value for field in view.build_embed().fields}
        self.assertEqual(fields["Bài nhà cái"], HIDDEN_DEALER_HAND)
        view.stop()

    async def test_selection_is_limited_to_three_cards(self) -> None:
        view, _ = make_view()
        for index in range(3):
            await view.toggle_card(make_interaction(), index)

        self.assertEqual(view.selected_indices, {0, 1, 2})
        self.assertFalse(view.draw_button.disabled)
        self.assertTrue(
            all(
                button.style is discord.ButtonStyle.primary
                for button in view.card_buttons[:3]
            )
        )

        fourth = make_interaction()
        await view.toggle_card(fourth, 3)
        self.assertEqual(view.selected_indices, {0, 1, 2})
        fourth.response.send_message.assert_awaited_once()

        await view.toggle_card(make_interaction(), 1)
        self.assertEqual(view.selected_indices, {0, 2})
        view.stop()

    async def test_draw_settles_win_once_and_unlocks_replay(self) -> None:
        view, cog = make_view(result=1)
        view.selected_indices = {0, 2}
        view._refresh_controls()
        interaction = make_interaction()

        await view.draw_button.callback(interaction)

        self.assertEqual(view.game.drawn_indices, (0, 2))
        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "poker",
            10,
            "poker-session",
            "win",
        )
        self.assertTrue(view.completed)
        self.assertFalse(view._closed)
        self.assertFalse(view.processing)
        self.assertTrue(view.draw_button.disabled)
        self.assertTrue(view.stand_button.disabled)
        self.assertTrue(all(button.disabled for button in view.card_buttons))
        self.assertFalse(view.play_again_button.disabled)
        self.assertFalse(view.bet_button.disabled)
        cog.unregister_game.assert_not_called()
        showdown = interaction.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("Chơi lại", showdown.description)
        fields = {field.name: field.value for field in showdown.fields}
        dealer_value = next(
            value for name, value in fields.items() if name.startswith("Bài nhà cái")
        )
        self.assertNotEqual(dealer_value, HIDDEN_DEALER_HAND)

        repeated = make_interaction()
        await view.stand_button.callback(repeated)
        cog.bank.credit.assert_called_once()
        repeated.response.send_message.assert_awaited_once()

    async def test_play_again_deals_a_new_hand_with_the_current_bet(self) -> None:
        view, cog = make_view(result=1)
        await view.stand_button.callback(make_interaction())
        interaction = make_interaction()

        await view.play_again_button.callback(interaction)

        cog.deal_hand.assert_called_once_with(
            user_id=42,
            guild_id=None,
            bet=5,
            session_id=view.session_id,
        )
        self.assertFalse(view.completed)
        self.assertFalse(view.game.finished)
        self.assertFalse(view.stand_button.disabled)
        self.assertFalse(view.card_buttons[0].disabled)
        self.assertTrue(view.play_again_button.disabled)
        self.assertEqual(view.balance, 90)
        interaction.response.edit_message.assert_awaited()

    async def test_bet_modal_updates_next_wager_after_a_hand(self) -> None:
        view, _ = make_view(result=1)
        await view.stand_button.callback(make_interaction())
        open_modal = make_interaction()
        await view.bet_button.callback(open_modal)
        open_modal.response.send_modal.assert_awaited_once()
        modal = open_modal.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, PokerBetModal)
        self.assertEqual(modal.amount.default, "5")

        modal.amount._value = "1,000"
        submit = make_interaction()
        await modal.on_submit(submit)
        self.assertEqual(view.bet, 1000)
        self.assertEqual(view.last_bet, 5)
        description = submit.response.edit_message.await_args.kwargs["embed"].description
        self.assertIn("1,000 TC", description)
        self.assertIn("10 TC", description)

    async def test_bet_cannot_change_during_an_open_hand(self) -> None:
        view, _ = make_view()
        interaction = make_interaction()
        await view.bet_button.callback(interaction)
        interaction.response.send_message.assert_awaited_once()
        interaction.response.send_modal.assert_not_awaited()
        self.assertEqual(view.bet, 5)

    async def test_push_returns_bet_and_loss_has_no_credit(self) -> None:
        push, push_cog = make_view(result=0)
        await push.stand_button.callback(make_interaction())
        push_cog.bank.credit.assert_called_once_with(
            42,
            None,
            "poker",
            5,
            "poker-session",
            "push",
        )

        loss, loss_cog = make_view(result=-1)
        await loss.stand_button.callback(make_interaction())
        loss_cog.bank.credit.assert_not_called()

    async def test_timeout_refunds_an_open_hand_once(self) -> None:
        view, cog = make_view()
        view.message = SimpleNamespace(edit=AsyncMock())

        await view.on_timeout()
        await view.on_timeout()

        cog.bank.credit.assert_called_once_with(
            42,
            None,
            "poker",
            5,
            "poker-session",
            "refund",
        )
        self.assertTrue(view.completed)
        self.assertTrue(view._closed)
        self.assertTrue(all(child.disabled for child in view.children))
        cog.unregister_game.assert_called_once_with(view)
        view.message.edit.assert_awaited_once()
        edit_kwargs = view.message.edit.await_args.kwargs
        self.assertIs(edit_kwargs["allowed_mentions"], NO_MENTIONS)

    async def test_timeout_after_settlement_does_not_refund(self) -> None:
        view, cog = make_view(result=1)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.stand_button.callback(make_interaction())
        cog.bank.credit.reset_mock()
        cog.unregister_game.reset_mock()

        await view.on_timeout()

        cog.bank.credit.assert_not_called()
        self.assertTrue(view._closed)
        cog.unregister_game.assert_called_once_with(view)
        view.message.edit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
