import unittest
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from cogs.utils._highlight_card import (
    CARD_WIDTH,
    HEADER_FONT_SIZE,
    MAX_CARD_HEIGHT,
    _drawable_channel_label,
    _prepare_attachment_image,
    format_highlight_channel_name,
    format_highlight_timestamp,
    normalize_highlight_text,
    render_highlight_card,
)
from cogs.utils._highlight_media import HighlightEmbed
from cogs.utils._quote_card import _UNSUPPORTED_GLYPH, _load_fallback_font


NOW = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


def _png_bytes(color=(200, 40, 40), size=(120, 80)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class TestHighlightText(unittest.TestCase):
    def test_normalizes_whitespace_and_custom_emoji(self):
        self.assertEqual(
            normalize_highlight_text(
                "  Xin   chào ✨  \n\n<a:waving:123456>  bạn "
            ),
            "Xin chào ✨\n\n:waving: bạn",
        )

    def test_empty_text_rejected_without_image(self):
        with self.assertRaisesRegex(ValueError, "chữ hoặc ảnh"):
            normalize_highlight_text(" \n\t ")

    def test_empty_text_allowed_when_image_present(self):
        self.assertEqual(
            normalize_highlight_text("   ", allow_empty=True),
            "",
        )


class TestHighlightTimestamp(unittest.TestCase):
    def test_same_ict_day_uses_today_label(self):
        created = datetime(2026, 9, 7, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(
            format_highlight_timestamp(created, now=NOW),
            "Hôm nay lúc 17:05",
        )

    def test_other_day_uses_dated_clock(self):
        created = datetime(2026, 9, 6, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(
            format_highlight_timestamp(created, now=NOW),
            "06/09/2026 21:00",
        )

    def test_naive_datetime_is_treated_as_utc(self):
        created = datetime(2026, 9, 7, 14, 30)
        self.assertEqual(
            format_highlight_timestamp(created, now=NOW),
            "Hôm nay lúc 21:30",
        )


class TestHighlightChannelName(unittest.TestCase):
    def test_plain_name_omits_hash_prefix(self):
        self.assertEqual(format_highlight_channel_name("general"), "general")
        self.assertEqual(format_highlight_channel_name("#general"), "general")

    def test_keeps_flag_emoji_instead_of_shortcode(self):
        self.assertEqual(
            format_highlight_channel_name("🇻🇳-chém-gió"),
            "🇻🇳-chém-gió",
        )
        self.assertNotIn(":Vietnam:", format_highlight_channel_name("🇻🇳-chat"))

    def test_box_drawing_and_cjk_brackets_become_ascii(self):
        self.assertEqual(
            format_highlight_channel_name("💬┃chém-gió"),
            "💬|chém-gió",
        )
        self.assertEqual(format_highlight_channel_name("「chat」"), "[chat]")
        self.assertEqual(format_highlight_channel_name("【chat】"), "[chat]")

    def test_fullwidth_and_empty_names(self):
        self.assertEqual(format_highlight_channel_name("＃general"), "general")
        self.assertEqual(format_highlight_channel_name("  \n  "), "channel")

    def test_header_font_draws_formatted_names_without_tofu(self):
        font = _load_fallback_font(HEADER_FONT_SIZE)
        names = (
            "💬┃chém-gió",
            "「chat」",
            "🇻🇳-chém-gió",
            "【announcements】",
        )
        for raw in names:
            with self.subTest(raw=raw):
                label = _drawable_channel_label(
                    format_highlight_channel_name(raw),
                    font,
                )
                drawn = "".join(run for run, _ in font._font_runs(label))
                self.assertNotIn(_UNSUPPORTED_GLYPH, drawn)
                self.assertFalse(label.startswith("#"))
                self.assertTrue(label)
                output = render_highlight_card(
                    avatar_bytes=None,
                    display_name="Kien",
                    channel_name=raw,
                    message_text="Tin nhắn hài",
                    timestamp_label="Hôm nay lúc 21:30",
                )
                self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))


class TestHighlightCardRendering(unittest.TestCase):
    def test_renders_png_with_expected_width(self):
        output = render_highlight_card(
            avatar_bytes=_png_bytes((80, 160, 220), (64, 64)),
            display_name="Người dùng ♡ 😀",
            channel_name="general",
            message_text="Xin chào ✨ Đây là highlight tiếng Việt",
            timestamp_label="Hôm nay lúc 21:30",
            accent_rgb=(120, 90, 220),
        )
        with Image.open(BytesIO(output)) as card:
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.size[0], CARD_WIDTH)
            self.assertGreaterEqual(card.size[1], 176)
            self.assertLessEqual(card.size[1], MAX_CARD_HEIGHT)

    def test_missing_avatar_uses_placeholder(self):
        output = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="chat",
            message_text="A short highlight.",
            timestamp_label="07/09/2026 21:30",
        )
        self.assertTrue(output.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_image_attachment_increases_card_height(self):
        text_only = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="memes",
            message_text="Caption",
            timestamp_label="Hôm nay lúc 21:30",
        )
        with_image = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="memes",
            message_text="Caption",
            timestamp_label="Hôm nay lúc 21:30",
            attachment_bytes=_png_bytes((12, 180, 90), (400, 240)),
        )
        text_height = Image.open(BytesIO(text_only)).size[1]
        image_height = Image.open(BytesIO(with_image)).size[1]
        self.assertGreater(image_height, text_height)

    def test_image_only_message_renders(self):
        output = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="pics",
            message_text="   ",
            timestamp_label="Hôm nay lúc 21:30",
            attachment_bytes=_png_bytes(),
        )
        with Image.open(BytesIO(output)) as card:
            self.assertEqual(card.size[0], CARD_WIDTH)

    def test_unreadable_attachment_without_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "chữ hoặc ảnh"):
            render_highlight_card(
                avatar_bytes=None,
                display_name="Kien",
                channel_name="pics",
                message_text="",
                timestamp_label="Hôm nay lúc 21:30",
                attachment_bytes=b"not-an-image",
            )

    def test_very_long_text_stays_within_height_cap(self):
        output = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="yap",
            message_text="từ " * 2_000,
            timestamp_label="Hôm nay lúc 21:30",
        )
        with Image.open(BytesIO(output)) as card:
            self.assertLessEqual(card.size[1], MAX_CARD_HEIGHT)
            self.assertEqual(card.size[0], CARD_WIDTH)


class TestHighlightMediaRendering(unittest.TestCase):
    def render(self, **kwargs) -> Image.Image:
        output = render_highlight_card(
            avatar_bytes=None,
            display_name="Kien",
            channel_name="highlights",
            timestamp_label="07/09/2026 21:30",
            **kwargs,
        )
        with Image.open(BytesIO(output)) as card:
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.width, CARD_WIDTH)
            self.assertLessEqual(card.height, MAX_CARD_HEIGHT)
            return card.copy()

    def assert_color_present(self, card: Image.Image, color: tuple[int, int, int]) -> None:
        colors = dict((value, count) for count, value in card.getcolors(card.width * card.height))
        self.assertGreater(colors.get(color, 0), 100)

    def test_multiple_attachment_images_are_all_visible(self):
        colors = [(233, 20, 30), (22, 233, 40), (33, 40, 233), (233, 190, 44)]
        card = self.render(
            message_text="Four photos",
            attachment_images=[_png_bytes(color) for color in colors],
        )
        for color in colors:
            self.assert_color_present(card, color)

    def test_embed_only_text_can_render_without_a_message_caption(self):
        card = self.render(
            message_text="",
            embeds=[HighlightEmbed(title="Event announcement", description="Welcome everyone!")],
        )
        self.assert_color_present(card, (0, 168, 252))

    def test_rich_embed_preserves_main_image_thumbnail_and_text_sections(self):
        from cogs.utils._quote_card import _FallbackFont

        drawn_text = []
        original = _FallbackFont.draw_multiline

        def record_text(font, draw, xy, lines, color, spacing):
            drawn_text.append(" ".join(lines))
            return original(font, draw, xy, lines, color, spacing)

        with patch.object(_FallbackFont, "draw_multiline", record_text):
            card = self.render(
                message_text="",
                embeds=[HighlightEmbed(
                    author_name="Community news",
                    title="Photo of the day",
                    description="A bright afternoon at the park.",
                    fields=(("Location", "Central Park"),) + tuple(
                        (f"Detail {index}", f"Value {index}") for index in range(1, 7)
                    ),
                    footer_text="Shared by the community",
                    color_rgb=(155, 12, 210),
                    image_bytes=_png_bytes((12, 180, 90), (640, 360)),
                    thumbnail_bytes=_png_bytes((235, 100, 25), (100, 100)),
                )],
            )
        for expected in (
            "Community news", "Photo of the day", "A bright afternoon at the park.",
            "Location", "Central Park", "Shared by the community", "Detail 6", "Value 6",
        ):
            self.assertIn(expected, drawn_text)
        self.assert_color_present(card, (12, 180, 90))
        self.assert_color_present(card, (235, 100, 25))
        self.assert_color_present(card, (155, 12, 210))

    def test_corrupt_embed_images_fall_back_to_embed_text(self):
        card = self.render(
            message_text="",
            embeds=[HighlightEmbed(title="Still readable", image_bytes=b"broken")],
        )
        self.assert_color_present(card, (0, 168, 252))

    def test_corrupt_media_falls_back_to_message_text(self):
        card = self.render(
            message_text="Caption survives",
            attachment_images=[b"broken"],
            embeds=[HighlightEmbed(image_bytes=b"broken", thumbnail_bytes=b"broken")],
        )
        self.assert_color_present(card, (219, 222, 225))

    def test_all_corrupt_media_without_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "chữ hoặc ảnh"):
            self.render(
                message_text="",
                attachment_images=[b"broken"],
                embeds=[HighlightEmbed(image_bytes=b"broken", thumbnail_bytes=b"broken")],
            )

    def test_long_caption_and_embeds_leave_all_images_visible_within_height_cap(self):
        colors = [(233, 20, 30), (22, 233, 40), (33, 40, 233), (233, 190, 44)]
        embed_colors = [(100, 20, 220), (220, 20, 100), (20, 220, 100), (100, 220, 20)]
        card = self.render(
            message_text="A very long caption " * 400,
            attachment_images=[_png_bytes(color, (900, 900)) for color in colors],
            embeds=[HighlightEmbed(
                author_name="Author " * 40,
                title="A lengthy title " * 40,
                description="Description " * 400,
                fields=tuple(("Field name " * 30, "Field value " * 100) for _ in range(25)),
                footer_text="Footer " * 100,
                image_bytes=_png_bytes(color, (900, 900)),
                thumbnail_bytes=_png_bytes(color, (120, 120)),
            ) for color in embed_colors],
        )
        for color in colors + embed_colors:
            self.assert_color_present(card, color)

    def test_images_and_embeds_are_capped_at_four_each(self):
        card = self.render(
            message_text="",
            attachment_images=[_png_bytes((22, 180, 80))] * 4 + [_png_bytes((210, 90, 13))],
            embeds=[HighlightEmbed(title="An embed")] * 4 + [HighlightEmbed(
                image_bytes=_png_bytes((190, 70, 33)),
            )],
        )
        colors = {value for _, value in card.getcolors(card.width * card.height)}
        self.assertNotIn((210, 90, 13), colors)
        self.assertNotIn((190, 70, 33), colors)

    def test_gif_uses_first_frame(self):
        data = BytesIO()
        first = Image.new("RGB", (100, 100), (200, 30, 80))
        second = Image.new("RGB", (100, 100), (30, 200, 80))
        first.save(data, "GIF", save_all=True, append_images=[second], duration=100, loop=0)
        card = self.render(message_text="", attachment_images=[data.getvalue()])
        self.assert_color_present(card, (200, 30, 80))
        colors = {value for _, value in card.getcolors(card.width * card.height)}
        self.assertNotIn((30, 200, 80), colors)

    def test_oversized_source_is_rejected_before_rendering(self):
        with patch("cogs.utils._highlight_card.MAX_IMAGE_PIXELS", 100):
            self.assertIsNone(_prepare_attachment_image(_png_bytes(), 400, 400))

    def test_decompression_bomb_is_treated_as_unreadable_media(self):
        with patch("cogs.utils._highlight_card.Image.open", side_effect=Image.DecompressionBombError):
            self.assertIsNone(_prepare_attachment_image(b"oversized-image", 400, 400))

    def test_extreme_aspect_ratios_remain_renderable_with_many_media_blocks(self):
        for size in ((20, 10_000), (10_000, 20)):
            with self.subTest(size=size):
                data = _png_bytes((200, 60, 120), size)
                self.render(
                    message_text="caption " * 500,
                    attachment_images=[data] * 4,
                    embeds=[HighlightEmbed(
                        title="Rich preview", description="Some text",
                        image_bytes=data, thumbnail_bytes=data,
                    )] * 4,
                )


if __name__ == "__main__":
    unittest.main()
