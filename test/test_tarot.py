import hashlib
import json
import random
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from cogs.funny_things.tarot._tarot_helpers import (
    ALL_CARDS,
    CARDS_BY_ID,
    DECK_SIZE,
    MAJOR_COUNT,
    MAX_QUESTION_LENGTH,
    MINOR_PER_SUIT,
    SPREAD_ORDER,
    SPREADS,
    DrawnCard,
    TarotReading,
    card_field_value,
    deal_reading,
    flip_all_row,
    how_to_flip_tarot,
    how_to_interpret_flip,
    how_to_read_tarot,
    major_short_en,
    parse_tarot_query,
    picker_spread_guide,
    resolve_spread,
    roman_numeral,
    spread_position_guide,
)
from cogs.funny_things.tarot._tarot_render import (
    BACK_FILENAME,
    CARD_SIZES,
    GOLD,
    TAROT_ASSET_DIR,
    back_art_path,
    card_art_path,
    render_tarot_spread,
    spread_art,
)
from cogs.funny_things.tarot._tarot_ui import (
    GUIDE_CUSTOM_ID,
    NO_MENTIONS,
    TAROT_TIMEOUT_SECONDS,
    TarotPickerView,
    TarotReadingView,
    build_guide_embed,
)
from cogs.funny_things.tarot.tarot import TarotCog


def sample_reading(key: str = "three", *, revealed: bool = False) -> TarotReading:
    spread = SPREADS[key]
    cards = tuple(
        DrawnCard(
            card=ALL_CARDS[index],
            reversed=index % 2 == 1,
            position=position,
        )
        for index, position in enumerate(spread.positions)
    )
    flags = [revealed] * spread.card_count
    return TarotReading(spread=spread, cards=cards, question="câu hỏi thử", revealed=flags)


def make_interaction(
    user_id: int = 42,
    *,
    custom_id: str | None = None,
) -> SimpleNamespace:
    acknowledged = {"done": False}

    async def acknowledge(*args, **kwargs):
        acknowledged["done"] = True

    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(id=99),
        data={"custom_id": custom_id} if custom_id else {},
        response=SimpleNamespace(
            send_message=AsyncMock(side_effect=acknowledge),
            edit_message=AsyncMock(side_effect=acknowledge),
            is_done=lambda: acknowledged["done"],
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def make_cog() -> TarotCog:
    cog = TarotCog(SimpleNamespace())
    cog.begin_reading = AsyncMock()
    return cog


def make_reading_view(key: str = "three") -> TarotReadingView:
    cog = SimpleNamespace()
    return TarotReadingView(
        cog,
        reading=sample_reading(key),
        owner_id=42,
        display_name="Người hỏi",
        avatar_url=None,
    )


def open_png(payload: bytes) -> Image.Image:
    image = Image.open(BytesIO(payload))
    image.load()
    return image


class TestTarotDeckAndSpreads(unittest.TestCase):
    def test_deck_is_complete_and_unique(self) -> None:
        self.assertEqual(len(ALL_CARDS), DECK_SIZE)
        self.assertEqual(len(CARDS_BY_ID), DECK_SIZE)
        self.assertEqual(sum(card.arcana == "major" for card in ALL_CARDS), MAJOR_COUNT)
        self.assertEqual(
            sum(card.arcana == "minor" for card in ALL_CARDS),
            MINOR_PER_SUIT * 4,
        )
        ids = [card.id for card in ALL_CARDS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(card.upright and card.reversed for card in ALL_CARDS))
        self.assertTrue(all(card.short_en and card.name_en for card in ALL_CARDS))
        self.assertEqual(major_short_en("The Fool"), "Fool")
        self.assertEqual(major_short_en("Wheel of Fortune"), "Wheel")
        self.assertEqual(major_short_en("The High Priestess"), "Priestess")

    def test_roman_numerals_cover_major_arcana(self) -> None:
        self.assertEqual(roman_numeral(0), "0")
        self.assertEqual(roman_numeral(1), "I")
        self.assertEqual(roman_numeral(4), "IV")
        self.assertEqual(roman_numeral(9), "IX")
        self.assertEqual(roman_numeral(14), "XIV")
        self.assertEqual(roman_numeral(19), "XIX")
        self.assertEqual(roman_numeral(21), "XXI")
        with self.assertRaises(ValueError):
            roman_numeral(22)

    def test_spread_sizes_and_positions_line_up(self) -> None:
        expected = {
            "single": 1,
            "three": 3,
            "five": 5,
            "seven": 7,
            "celtic": 10,
        }
        self.assertEqual(tuple(SPREADS), SPREAD_ORDER)
        for key, count in expected.items():
            spread = SPREADS[key]
            self.assertEqual(spread.card_count, count)
            self.assertEqual(
                [position.index for position in spread.positions],
                list(range(1, count + 1)),
            )
            art = spread_art(key)
            self.assertEqual(len(art.slots), count)

    def test_parse_query_reads_aliases_or_keeps_question(self) -> None:
        spread, question = parse_tarot_query("")
        self.assertIsNone(spread)
        self.assertEqual(question, "")

        spread, question = parse_tarot_query("celtic tình cảm của mình")
        self.assertEqual(spread.key, "celtic")
        self.assertEqual(question, "tình cảm của mình")

        spread, question = parse_tarot_query("3")
        self.assertEqual(spread.key, "three")
        self.assertEqual(question, "")

        spread, question = parse_tarot_query("hôm nay ra sao")
        self.assertIsNone(spread)
        self.assertEqual(question, "hôm nay ra sao")

        self.assertEqual(resolve_spread("1lá").key, "single")
        self.assertEqual(resolve_spread("Celtic-Cross").key, "celtic")
        self.assertEqual(resolve_spread("móng ngựa").key, "seven")
        self.assertIsNone(resolve_spread("nam nay"))

    def test_question_is_collapsed_and_capped(self) -> None:
        _, question = parse_tarot_query("   hỏi    dài   ")
        self.assertEqual(question, "hỏi dài")
        huge = "á" * (MAX_QUESTION_LENGTH + 40)
        _, question = parse_tarot_query(huge)
        self.assertEqual(len(question), MAX_QUESTION_LENGTH)
        self.assertTrue(question.endswith("…"))

    def test_deal_is_seeded_without_replacement(self) -> None:
        first = deal_reading(SPREADS["celtic"], "seed", rng=random.Random(7))
        second = deal_reading(SPREADS["celtic"], "seed", rng=random.Random(7))
        self.assertEqual(first.cards, second.cards)
        ids = [drawn.card.id for drawn in first.cards]
        self.assertEqual(len(ids), len(set(ids)))
        other = deal_reading(SPREADS["celtic"], "seed", rng=random.Random(8))
        self.assertNotEqual(first.cards, other.cards)

    def test_flip_one_then_flip_all(self) -> None:
        reading = sample_reading("five")
        self.assertTrue(reading.flip(0))
        self.assertFalse(reading.flip(0))
        self.assertEqual(reading.revealed_count, 1)
        self.assertEqual(reading.flip_all(), 4)
        self.assertTrue(reading.all_revealed)
        self.assertEqual(reading.flip_all(), 0)
        with self.assertRaises(IndexError):
            reading.flip(5)

    def test_flip_all_row_shares_last_row_when_possible(self) -> None:
        self.assertEqual(flip_all_row(1), 0)
        self.assertEqual(flip_all_row(3), 0)
        self.assertEqual(flip_all_row(5), 1)
        self.assertEqual(flip_all_row(7), 1)
        self.assertEqual(flip_all_row(10), 2)

    def test_revealed_embed_fields_stay_within_discord_limits(self) -> None:
        for key in SPREAD_ORDER:
            reading = sample_reading(key, revealed=True)
            view = TarotReadingView(
                SimpleNamespace(),
                reading=reading,
                owner_id=1,
                display_name="Tester",
                avatar_url=None,
            )
            embed = view.build_embed()
            self.assertLessEqual(len(embed), 6000)
            self.assertLessEqual(len(embed.description or ""), 4096)
            self.assertEqual(len(embed.fields), reading.spread.card_count)
            for field in embed.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)
            view.stop()

        longest = max(
            ALL_CARDS,
            key=lambda card: len(card_field_value(
                DrawnCard(card=card, reversed=True, position=SPREADS["single"].positions[0]),
                True,
            )),
        )
        value = card_field_value(
            DrawnCard(
                card=longest,
                reversed=True,
                position=SPREADS["single"].positions[0],
            ),
            True,
        )
        self.assertLessEqual(len(value), 1024)
        self.assertIn(longest.name_en, value)
        self.assertNotIn(longest.name_vi, value)

    def test_guide_copy_stays_within_discord_field_limits(self) -> None:
        self.assertIn("úp", how_to_read_tarot())
        self.assertIn("Lật tất cả", how_to_flip_tarot())
        self.assertIn("số vàng", how_to_flip_tarot())
        self.assertIn("tiếng Anh", how_to_interpret_flip())
        self.assertIn("Ngược", how_to_interpret_flip())
        self.assertLessEqual(len(how_to_flip_tarot()), 1024)
        self.assertLessEqual(len(how_to_interpret_flip()), 1024)
        self.assertLessEqual(len(picker_spread_guide()), 1024)
        for key in SPREAD_ORDER:
            with self.subTest(spread=key):
                guide = spread_position_guide(SPREADS[key])
                self.assertLessEqual(len(guide), 1024)
                embed = build_guide_embed(SPREADS[key])
                self.assertLessEqual(len(embed), 6000)
                self.assertLessEqual(len(embed.description or ""), 4096)
                self.assertEqual(
                    [field.name for field in embed.fields[:2]],
                    ["Cách lật bài", "Đọc lá đã lật"],
                )
                for field in embed.fields:
                    self.assertLessEqual(len(field.name), 256)
                    self.assertLessEqual(len(field.value), 1024)
                self.assertIn(
                    SPREADS[key].positions[0].name_vi,
                    embed.fields[-1].value,
                )


class TestTarotRender(unittest.TestCase):
    def test_hidden_and_revealed_spreads_are_pngs(self) -> None:
        hidden = sample_reading("celtic")
        shown = sample_reading("celtic", revealed=True)
        hidden_png = render_tarot_spread(hidden)
        shown_png = render_tarot_spread(shown)
        hidden_image = open_png(hidden_png)
        shown_image = open_png(shown_png)
        art = spread_art("celtic")
        self.assertTrue(hidden_png.startswith(b"\x89PNG"))
        self.assertEqual(hidden_image.size, (art.width, art.height))
        self.assertEqual(shown_image.size, (art.width, art.height))
        self.assertNotEqual(hidden_image.tobytes(), shown_image.tobytes())

    def test_every_spread_renders(self) -> None:
        for key in SPREAD_ORDER:
            with self.subTest(spread=key):
                png = render_tarot_spread(sample_reading(key, revealed=True))
                image = open_png(png)
                art = spread_art(key)
                self.assertEqual(image.size, (art.width, art.height))
                self.assertEqual(image.mode, "RGB")

    def test_card_bezel_is_pastel_pink(self) -> None:
        shown = open_png(render_tarot_spread(sample_reading("single", revealed=True)))
        hidden = open_png(render_tarot_spread(sample_reading("single", revealed=False)))
        slot = spread_art("single").slots[0]
        width, height = CARD_SIZES["single"]
        sample = (slot.cx, slot.cy - height // 2 + 2)
        for image in (shown, hidden):
            red, green, blue = image.getpixel(sample)[:3]
            self.assertGreater(red, 180)
            self.assertGreater(blue, 140)
            self.assertLess(abs(green - blue), 80)
            gold_distance = abs(red - GOLD[0]) + abs(green - GOLD[1]) + abs(blue - GOLD[2])
            self.assertGreater(gold_distance, 80)

    def test_bundled_rws_art_covers_the_deck(self) -> None:
        manifest = json.loads(
            (TAROT_ASSET_DIR / "sources.json").read_text(encoding="utf-8")
        )
        files = {entry["file"]: entry["sha256"] for entry in manifest["cards"]}
        self.assertEqual(len(files), DECK_SIZE + 1)
        self.assertEqual(manifest["license"], "Public domain")
        self.assertTrue(back_art_path().is_file())
        self.assertEqual(
            hashlib.sha256(back_art_path().read_bytes()).hexdigest(),
            files[BACK_FILENAME],
        )
        for card in ALL_CARDS:
            path = card_art_path(card)
            self.assertTrue(path.is_file(), card.id)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                files[card.asset_filename],
                card.id,
            )


class TestTarotViews(unittest.IsolatedAsyncioTestCase):
    async def test_reading_is_owner_only_and_starts_face_down(self) -> None:
        view = make_reading_view("three")
        owner = make_interaction()
        stranger = make_interaction(99)

        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once()
        self.assertEqual(view.timeout, TAROT_TIMEOUT_SECONDS)
        self.assertEqual(len(view.card_buttons), 3)
        self.assertFalse(view.flip_all_button.disabled)
        embed = view.build_embed()
        self.assertTrue(all("Chưa lật" in field.value for field in embed.fields))
        png = view._render_png()
        self.assertTrue(png.startswith(b"\x89PNG"))
        view.stop()

    async def test_flip_one_card_then_flip_all(self) -> None:
        view = make_reading_view("three")
        interaction = make_interaction()

        await view.card_buttons[1].callback(interaction)

        self.assertEqual(view.reading.revealed, [False, True, False])
        self.assertTrue(view.card_buttons[1].disabled)
        self.assertFalse(view.flip_all_button.disabled)
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["allowed_mentions"], NO_MENTIONS)
        self.assertEqual(len(kwargs["attachments"]), 1)
        self.assertEqual(kwargs["embed"].fields[1].name, "2. Hiện tại")
        self.assertIn("The Magician", kwargs["embed"].fields[1].value)
        self.assertNotIn("Nhà Ảo Thuật", kwargs["embed"].fields[1].value)

        await view.flip_all_button.callback(make_interaction())
        self.assertTrue(view.reading.all_revealed)
        self.assertTrue(view.flip_all_button.disabled)
        self.assertTrue(all(button.disabled for button in view.card_buttons))
        view.stop()

    async def test_flip_all_on_one_card_spread(self) -> None:
        view = make_reading_view("single")
        await view.flip_all_button.callback(make_interaction())
        self.assertTrue(view.reading.all_revealed)
        self.assertTrue(view.card_buttons[0].disabled)
        view.stop()

    async def test_celtic_cross_uses_three_button_rows(self) -> None:
        view = make_reading_view("celtic")
        self.assertEqual(len(view.card_buttons), 10)
        self.assertEqual(view.card_buttons[0].row, 0)
        self.assertEqual(view.card_buttons[5].row, 1)
        self.assertEqual(view.flip_all_button.row, 2)
        self.assertEqual(view.guide_button.row, 2)
        self.assertEqual(view.guide_button.custom_id, GUIDE_CUSTOM_ID)
        view.stop()

    async def test_anyone_can_open_the_guide(self) -> None:
        view = make_reading_view("three")
        stranger = make_interaction(99, custom_id=GUIDE_CUSTOM_ID)

        self.assertTrue(await view.interaction_check(stranger))
        self.assertFalse(await view.interaction_check(make_interaction(99)))

        await view.guide_button.callback(stranger)
        kwargs = stranger.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        fields = {field.name: field.value for field in kwargs["embed"].fields}
        self.assertIn("Lật tất cả", fields["Cách lật bài"])
        self.assertIn("tiếng Anh", fields["Đọc lá đã lật"])
        self.assertIn("Quá khứ", fields["Các vị trí — Ba lá"])
        self.assertIn("Hiện tại", fields["Các vị trí — Ba lá"])
        view.stop()

    async def test_picker_is_owner_only(self) -> None:
        view = TarotPickerView(
            make_cog(),
            owner_id=42,
            display_name="Người hỏi",
            avatar_url=None,
            question="hôm nay",
        )
        self.assertFalse(await view.interaction_check(make_interaction(7)))
        self.assertTrue(await view.interaction_check(make_interaction(42)))
        embed = view.build_embed()
        self.assertEqual(len(embed.fields), len(SPREAD_ORDER))
        self.assertIn("hôm nay", embed.description or "")
        self.assertEqual(view.guide_button.custom_id, GUIDE_CUSTOM_ID)
        view.stop()

    async def test_picker_starts_reading_for_selected_spread(self) -> None:
        cog = make_cog()
        view = TarotPickerView(
            cog,
            owner_id=42,
            display_name="Người hỏi",
            avatar_url=None,
            question="việc học",
        )
        view.spread_select = view.children[0]
        view.spread_select._values = ["celtic"]
        interaction = make_interaction()

        await view.spread_select.callback(interaction)

        cog.begin_reading.assert_awaited_once()
        kwargs = cog.begin_reading.await_args.kwargs
        self.assertEqual(kwargs["spread"].key, "celtic")
        self.assertEqual(kwargs["question"], "việc học")
        self.assertTrue(view._closed)
        view.stop()


class TestTarotCommand(unittest.IsolatedAsyncioTestCase):
    async def test_command_opens_picker_when_spread_is_omitted(self) -> None:
        cog = TarotCog(SimpleNamespace())
        ctx = SimpleNamespace(
            author=SimpleNamespace(
                id=42,
                name="user",
                display_name="Người hỏi",
                display_avatar=SimpleNamespace(url="https://example.test/a.png"),
            ),
            send=AsyncMock(return_value=SimpleNamespace(id=1)),
        )

        await TarotCog.tarot.callback(cog, ctx, query="hôm nay thế nào")

        kwargs = ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"], TarotPickerView)
        self.assertIn("hôm nay thế nào", kwargs["embed"].description)
        kwargs["view"].stop()

    async def test_command_deals_named_spread(self) -> None:
        cog = TarotCog(SimpleNamespace())
        ctx = SimpleNamespace(
            author=SimpleNamespace(
                id=42,
                name="user",
                display_name="Người hỏi",
                display_avatar=SimpleNamespace(url=None),
            ),
            send=AsyncMock(return_value=SimpleNamespace(id=1)),
        )

        await TarotCog.tarot.callback(cog, ctx, query="3 chuyện cũ")

        kwargs = ctx.send.await_args.kwargs
        view = kwargs["view"]
        self.assertIsInstance(view, TarotReadingView)
        self.assertEqual(view.reading.spread.key, "three")
        self.assertEqual(view.reading.question, "chuyện cũ")
        self.assertFalse(any(view.reading.revealed))
        self.assertEqual(kwargs["file"].filename, "tarot.png")
        view.stop()


if __name__ == "__main__":
    unittest.main()
