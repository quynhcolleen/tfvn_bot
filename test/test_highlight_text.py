import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from cogs.utils._highlight_text import (
    CODE_BACKGROUND,
    EmbedTextFont,
    TextLine,
    TextStyle,
    parse_embed_text,
    truncate_embed_line,
    wrap_embed_text,
)


class TestEmbedMarkdownParsing(unittest.TestCase):
    def plain_text(self, content: str) -> str:
        return "".join(run.text for run in parse_embed_text(content))

    def test_meter_percentage_has_bold_style_without_literal_markers(self):
        runs = parse_embed_text("████░░ **68%**")
        self.assertEqual("".join(run.text for run in runs), "████░░ 68%")
        self.assertEqual(runs[-1].text, "68%")
        self.assertTrue(runs[-1].style.bold)

    def test_fenced_vietnamese_result_is_code(self):
        runs = parse_embed_text("```Bóng lộ bà ơi!```")
        self.assertEqual(runs[0].text, "Bóng lộ bà ơi!")
        self.assertTrue(runs[0].style.code)

    def test_code_keeps_indentation_newlines_and_literal_markdown(self):
        text = "```python\n  **literal**\n\n    snake_case = `<@123>`\n```"
        runs = parse_embed_text(text)
        self.assertEqual(runs[0].text, "  **literal**\n\n    snake_case = `<@123>`")
        self.assertEqual(runs[0].style, TextStyle(code=True))

    def test_code_block_starts_on_a_separate_line(self):
        self.assertEqual(self.plain_text("before```result```after"), "before\nresult\nafter")

    def test_inline_code_keeps_delimiters_and_escapes_literal(self):
        runs = parse_embed_text(r"Use `**name**_\*` here")
        self.assertEqual(runs[1].text, r"**name**_\*")
        self.assertEqual(runs[1].style, TextStyle(code=True))

    def test_double_backticks_allow_a_literal_backtick_in_code(self):
        runs = parse_embed_text("``a `literal` **value**``")
        self.assertEqual(runs[0].text, "a `literal` **value**")
        self.assertTrue(runs[0].style.code)

    def test_unclosed_code_markers_remain_visible(self):
        self.assertEqual(self.plain_text("```unfinished"), "```unfinished")

    def test_masked_link_displays_its_styled_label(self):
        runs = parse_embed_text("Read [**our guide**](https://example.com/our_guide) now")
        self.assertEqual("".join(run.text for run in runs), "Read our guide now")
        self.assertTrue(runs[1].style.link)
        self.assertTrue(runs[1].style.bold)

    def test_masked_link_supports_parentheses_in_its_target(self):
        self.assertEqual(self.plain_text("[guide](https://example.com/a_(b))"), "guide")

    def test_escaped_markdown_in_display_names_is_literal(self):
        text = r"@\*\*literal\_name\*\* and \*literal\*"
        runs = parse_embed_text(text)
        self.assertEqual("".join(run.text for run in runs), "@**literal_name** and *literal*")
        self.assertTrue(all(run.style == TextStyle() for run in runs))

    def test_literal_underscores_urls_and_unmatched_delimiters_survive(self):
        text = "snake_case_key https://example.com/a_b_c?q=x_y *unfinished"
        self.assertEqual(self.plain_text(text), text)

    def test_bare_url_can_be_surrounded_by_bold_markers(self):
        runs = parse_embed_text("**https://example.com/a_b_c**")
        self.assertEqual("".join(run.text for run in runs), "https://example.com/a_b_c")
        self.assertTrue(runs[0].style.bold)
        self.assertTrue(runs[0].style.link)

    def test_supported_styles_and_nested_bold(self):
        runs = parse_embed_text("*italic* __underlined__ ~~removed~~ ***both***")
        self.assertEqual("".join(run.text for run in runs), "italic underlined removed both")
        visible = {run.text: run.style for run in runs if run.text.strip()}
        self.assertTrue(visible["italic"].italic)
        self.assertTrue(visible["underlined"].underline)
        self.assertTrue(visible["removed"].strike)
        self.assertTrue(visible["both"].bold)
        self.assertTrue(visible["both"].italic)

    def test_italic_span_nested_at_end_of_bold_span(self):
        runs = parse_embed_text("**bold and *italic***")
        self.assertEqual("".join(run.text for run in runs), "bold and italic")
        self.assertTrue(runs[0].style.bold)
        self.assertTrue(runs[-1].style.bold)
        self.assertTrue(runs[-1].style.italic)

    def test_bold_span_nested_inside_italic_span(self):
        runs = parse_embed_text("*italic **bold** end*")
        self.assertEqual("".join(run.text for run in runs), "italic bold end")
        self.assertTrue(all(run.style.italic for run in runs))
        self.assertEqual([run.text for run in runs if run.style.bold], ["bold"])

    def test_bold_span_nested_at_end_of_italic_span(self):
        runs = parse_embed_text("*italic and **bold***")
        self.assertEqual("".join(run.text for run in runs), "italic and bold")
        self.assertTrue(all(run.style.italic for run in runs))
        self.assertTrue(runs[-1].style.bold)

    def test_inline_code_asterisks_do_not_close_surrounding_bold(self):
        runs = parse_embed_text("**bold `**literal**` end**")
        self.assertEqual("".join(run.text for run in runs), "bold **literal** end")
        self.assertEqual(runs[1].style, TextStyle(code=True))
        self.assertTrue(runs[0].style.bold)
        self.assertTrue(runs[-1].style.bold)

    def test_code_fence_delimiters_do_not_close_surrounding_italic(self):
        runs = parse_embed_text("*before```*literal*```after*")
        self.assertEqual("".join(run.text for run in runs), "before\n*literal*\nafter")
        self.assertEqual(runs[1].style, TextStyle(code=True))
        self.assertTrue(runs[0].style.italic)
        self.assertTrue(runs[-1].style.italic)

    def test_masked_link_delimiters_do_not_close_surrounding_bold(self):
        runs = parse_embed_text("**before [**guide**](https://example.com/a_**b**) after**")
        self.assertEqual("".join(run.text for run in runs), "before guide after")
        self.assertTrue(all(run.style.bold for run in runs))
        self.assertTrue(runs[1].style.link)

    def test_escaped_delimiters_do_not_close_surrounding_style(self):
        runs = parse_embed_text(r"**before \*\*literal\_name\*\* after**")
        self.assertEqual("".join(run.text for run in runs), "before **literal_name** after")
        self.assertTrue(all(run.style.bold for run in runs))

    def test_pathological_unmatched_nesting_falls_back_to_literal_text(self):
        text = ("*x **y ***z _a __b ~~c " * 70)[:1200]
        self.assertEqual(self.plain_text(text), text)


class TestEmbedTextLayout(unittest.TestCase):
    def setUp(self):
        self.font = EmbedTextFont(24)

    def test_vietnamese_code_spacing_is_compact(self):
        text = "Bóng lộ bà ơi!"
        line = TextLine(parse_embed_text(f"`{text}`"))
        self.assertLess(self.font.getlength(line), len(text) * 24 * 0.85)

    def test_code_keeps_combining_marks_and_rainbow_flag_together(self):
        lines = wrap_embed_text("`a\u0301 🏳️‍🌈`", self.font, 500)
        canvas = Image.new("RGB", (520, 80), (43, 45, 49))
        with patch.object(
            self.font.regular, "draw_text", wraps=self.font.regular.draw_text,
        ) as draw_text:
            self.font.draw_multiline(ImageDraw.Draw(canvas), (10, 10), lines, (255, 255, 255), 10)
        glyphs = [call.args[2] for call in draw_text.call_args_list]
        self.assertIn("a\u0301", glyphs)
        self.assertIn("🏳️‍🌈", glyphs)

    def test_wrapping_preserves_styles_and_stays_inside_measured_width(self):
        lines = wrap_embed_text("hello **verylongboldpercentage68%** `code_value` *italic text*", self.font, 120)
        self.assertGreater(len(lines), 3)
        self.assertTrue(all(self.font.getlength(line) <= 120 for line in lines))
        self.assertTrue(any(run.style.bold for line in lines for run in line.runs))
        self.assertTrue(any(run.style.code for line in lines for run in line.runs))
        self.assertTrue(any(run.style.italic for line in lines for run in line.runs))

    def test_code_layout_preserves_indent_and_empty_lines(self):
        lines = wrap_embed_text("```\n  first\n\n    second\n```", self.font, 500)
        self.assertEqual([line.text for line in lines], ["  first", "", "    second"])

    def test_truncation_keeps_bold_percentage_style_and_bounded_ellipsis(self):
        line = TextLine(parse_embed_text("**68% long result text**"))
        result = truncate_embed_line(line, self.font, 120)
        self.assertTrue(result.text.endswith("…"))
        self.assertTrue(result.runs[0].style.bold)
        self.assertLessEqual(self.font.getlength(result), 120)

    def test_percentage_is_drawn_with_bold_font_and_code_has_background(self):
        lines = wrap_embed_text("**68%**\n```Bóng lộ bà ơi!```", self.font, 500)
        canvas = Image.new("RGB", (520, 150), (43, 45, 49))
        with patch.object(self.font.bold, "draw_text", wraps=self.font.bold.draw_text) as bold_draw:
            self.font.draw_multiline(ImageDraw.Draw(canvas), (10, 10), lines, (219, 222, 225), 10)
        self.assertTrue(any(call.args[2] == "68%" for call in bold_draw.call_args_list))
        colors = {color for _, color in canvas.getcolors(canvas.width * canvas.height)}
        self.assertIn(CODE_BACKGROUND, colors)

    def test_styled_pixels_fit_the_reported_layout_height(self):
        lines = wrap_embed_text("**68%** *italic* __underlined__ ~~removed~~\n```value```", self.font, 280)
        bounds = self.font.multiline_bbox(lines, 10)
        canvas = Image.new("RGB", (300, 250), (0, 0, 0))
        self.font.draw_multiline(ImageDraw.Draw(canvas), (0, 0), lines, (255, 255, 255), 10)
        drawn = canvas.getbbox()
        self.assertIsNotNone(drawn)
        self.assertLessEqual(drawn[2], 280)
        self.assertLessEqual(drawn[3], bounds[3] + 1)


if __name__ == "__main__":
    unittest.main()
