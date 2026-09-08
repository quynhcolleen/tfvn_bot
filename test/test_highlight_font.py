import math
import unittest

from PIL import Image, ImageDraw

from cogs.utils._highlight_card import normalize_highlight_text
from cogs.utils._highlight_font import load_highlight_font
from cogs.utils._quote_card import (
    MAX_NORMALIZED_TEXT_LENGTH,
    _UNSUPPORTED_GLYPH,
    wrap_quote_text,
)


class TestHighlightUnicode(unittest.TestCase):
    def test_composite_emoji_are_preserved(self):
        text = "🏳️‍🌈 Gay Meter 🏳️‍🌈 🇻🇳 👍🏽"
        self.assertEqual(normalize_highlight_text(text), text)

    def test_preserves_meter_blocks(self):
        text = "█" * 13 + "░" * 7 + " **68%**"
        self.assertEqual(normalize_highlight_text(text), text)

    def test_preserves_code_spacing_when_requested(self):
        text = "```\r\n  first\r\n\r\n\r\n    second\r\n```"
        self.assertEqual(
            normalize_highlight_text(text, preserve_whitespace=True),
            "```\n  first\n\n\n    second\n```",
        )

    def test_code_normalization_keeps_limits_and_empty_behavior(self):
        for preserve_whitespace in (False, True):
            with self.subTest(preserve_whitespace=preserve_whitespace):
                result = normalize_highlight_text(
                    "x" * 10_000, preserve_whitespace=preserve_whitespace,
                )
                self.assertEqual(len(result), MAX_NORMALIZED_TEXT_LENGTH)
                self.assertTrue(result.endswith("…"))
                self.assertEqual(
                    normalize_highlight_text(
                        " \n\t ", allow_empty=True,
                        preserve_whitespace=preserve_whitespace,
                    ),
                    "",
                )


class TestHighlightGlyphs(unittest.TestCase):
    def test_meter_does_not_use_replacement_glyphs(self):
        font = load_highlight_font(24)
        text = "█" * 13 + "░" * 7 + " 68%"
        rendered = "".join(run for run, _ in font._font_runs(text))
        self.assertEqual(rendered, text)
        self.assertNotIn(_UNSUPPORTED_GLYPH, rendered)

    def test_meter_wraps_to_measured_width(self):
        font = load_highlight_font(24)
        text = "█" * 13 + "░" * 7
        width = math.ceil(font.getlength("█" * 10))
        lines = wrap_quote_text(text, font, width)
        self.assertEqual("".join(lines), text)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(font.getlength(line) <= width for line in lines))

    def test_block_and_shading_have_distinct_visible_coverage(self):
        font = load_highlight_font(24)
        pixel_counts = []
        for glyph in ("░", "▒", "▓", "█"):
            canvas = Image.new("RGB", (80, 60), (0, 0, 0))
            font.draw_text(ImageDraw.Draw(canvas), (5, 5), glyph, (255, 255, 255))
            pixels = sum(
                count for count, color in canvas.getcolors()
                if color == (255, 255, 255)
            )
            pixel_counts.append(pixels)
            bounds = canvas.getbbox()
            self.assertIsNotNone(bounds)
            self.assertLessEqual(bounds[2] - bounds[0], math.ceil(font.getlength(glyph)))
        self.assertTrue(all(a < b for a, b in zip(pixel_counts, pixel_counts[1:])))
        self.assertAlmostEqual(pixel_counts[0] / pixel_counts[-1], 0.25, delta=0.05)

    def test_rainbow_flag_is_one_colored_symbol_without_raqm(self):
        font = load_highlight_font(24, bold=True)
        for flag in ("🏳️‍🌈", "🏳‍🌈"):
            with self.subTest(flag=flag):
                self.assertLess(font.getlength(flag), font.getlength("🏳 🌈"))
                canvas = Image.new("RGB", (80, 60), (0, 0, 0))
                font.draw_text(ImageDraw.Draw(canvas), (5, 5), flag, (255, 255, 255))
                colors = {color for count, color in canvas.getcolors()}
                # All six stripes must survive drawing on a BASIC-layout build.
                self.assertTrue({
                    (229, 57, 53), (251, 140, 0), (253, 216, 53),
                    (67, 160, 71), (30, 136, 229), (142, 36, 170),
                }.issubset(colors))
                self.assertLessEqual(canvas.getbbox()[2] - 5, math.ceil(font.getlength(flag)))

    def test_mixed_content_ink_fits_measured_bounds(self):
        font = load_highlight_font(24)
        text = "Kết quả 🏳️‍🌈 " + "█" * 13 + "░" * 7 + " 68%"
        bbox = font._text_bbox(text)
        canvas = Image.new("RGB", (math.ceil(bbox[2]) + 20, 80), (0, 0, 0))
        font.draw_text(ImageDraw.Draw(canvas), (10, 10), text, (255, 255, 255))
        ink = canvas.getbbox()
        self.assertGreaterEqual(ink[1], math.floor(bbox[1]) + 10)
        self.assertLessEqual(ink[2], math.ceil(bbox[2]) + 10)
        self.assertLessEqual(ink[3], math.ceil(bbox[3]) + 10)


if __name__ == "__main__":
    unittest.main()
