import unittest
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image

from cogs.utils._highlight_card import (
    CARD_WIDTH,
    HEADER_FONT_SIZE,
    MAX_CARD_HEIGHT,
    _drawable_channel_label,
    format_highlight_channel_name,
    format_highlight_timestamp,
    normalize_highlight_text,
    render_highlight_card,
)
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


if __name__ == "__main__":
    unittest.main()
