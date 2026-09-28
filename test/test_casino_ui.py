import unittest
from io import BytesIO

from PIL import Image

from cogs.minigames._casino_ui import (
    TABLE_HEIGHT,
    TABLE_WIDTH,
    render_blackjack_table,
    render_poker_table,
    render_sicbo_table,
    render_slot_table,
)
from cogs.minigames._playing_cards import Card


PLAYER = [Card(14, "♠"), Card(13, "♥")]
DEALER = [Card(10, "♦"), Card(9, "♣")]
POKER_HAND = [
    Card(10, "♠"),
    Card(11, "♠"),
    Card(12, "♠"),
    Card(13, "♠"),
    Card(14, "♠"),
]


def open_png(payload: bytes) -> Image.Image:
    image = Image.open(BytesIO(payload))
    image.load()
    return image


class TestCasinoTables(unittest.TestCase):
    def test_blackjack_table_hides_hole_card_until_reveal(self) -> None:
        hidden = render_blackjack_table(
            player_hand=PLAYER,
            dealer_hand=DEALER,
            player_total=21,
            dealer_total=None,
            reveal_dealer=False,
            bet=5,
            balance=95,
            status="Rút bài hoặc Dừng",
        )
        shown = render_blackjack_table(
            player_hand=PLAYER,
            dealer_hand=DEALER,
            player_total=21,
            dealer_total=19,
            reveal_dealer=True,
            bet=5,
            balance=105,
            status="Bạn thắng nhà cái!",
        )
        hidden_image = open_png(hidden)
        shown_image = open_png(shown)
        self.assertEqual(hidden_image.size, (TABLE_WIDTH, TABLE_HEIGHT))
        self.assertEqual(shown_image.size, (TABLE_WIDTH, TABLE_HEIGHT))
        self.assertEqual(hidden_image.mode, "RGB")
        self.assertNotEqual(hidden_image.tobytes(), shown_image.tobytes())

    def test_poker_selection_changes_player_row(self) -> None:
        plain = render_poker_table(
            player_hand=POKER_HAND,
            dealer_hand=POKER_HAND,
            reveal_dealer=False,
            selected=(),
            bet=5,
            balance=95,
            status="Chưa chọn lá nào.",
        )
        selected = render_poker_table(
            player_hand=POKER_HAND,
            dealer_hand=POKER_HAND,
            reveal_dealer=False,
            selected=(0, 2),
            bet=5,
            balance=95,
            status="Đổi lá số 1, 3.",
        )
        self.assertNotEqual(open_png(plain).tobytes(), open_png(selected).tobytes())

    def test_slot_and_sicbo_boards_are_fixed_size_pngs(self) -> None:
        slot = open_png(
            render_slot_table(
                reels=("seven", "seven", "seven"),
                bet=5,
                balance=100,
                status="NỔ HŨ!",
            )
        )
        spinning = open_png(
            render_slot_table(
                reels=("cherry", "bell", "bar"),
                bet=5,
                balance=95,
                status="Đang quay...",
                spinning=True,
            )
        )
        waiting = open_png(
            render_sicbo_table(
                dice=None,
                bet=5,
                balance=95,
                choice=None,
                status="Chọn Tài, Xỉu hoặc Bộ ba",
            )
        )
        rolled = open_png(
            render_sicbo_table(
                dice=(6, 5, 4),
                bet=5,
                balance=105,
                choice="big",
                status="Thắng Tài!",
            )
        )
        for image in (slot, spinning, waiting, rolled):
            self.assertEqual(image.size, (TABLE_WIDTH, TABLE_HEIGHT))
            self.assertEqual(image.mode, "RGB")
        self.assertNotEqual(slot.tobytes(), spinning.tobytes())
        self.assertNotEqual(waiting.tobytes(), rolled.tobytes())
        triple = open_png(
            render_sicbo_table(
                dice=(4, 4, 4),
                bet=5,
                balance=95,
                choice="triple",
                status="Thắng Bộ ba!",
            )
        )
        self.assertNotEqual(rolled.tobytes(), triple.tobytes())


if __name__ == "__main__":
    unittest.main()
