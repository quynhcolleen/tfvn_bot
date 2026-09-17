"""
Unit tests for number-based meter helpers (aura / redflag).

Asserts count-matching icon bars (not percent maps) and signed score
formatting with no percent in the primary score string path.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "cogs" / "funny_things" / "meters" / "_meter_helper.py"
AURA_PATH = ROOT / "cogs" / "funny_things" / "meters" / "aura.py"
REDFLAG_PATH = ROOT / "cogs" / "funny_things" / "meters" / "redflag.py"


def _load_module(name, path):
    """Load a module by file path (works without package install)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pure helper has no discord dependency at import time
helper = _load_module("funny_things_meter_helper", HELPER_PATH)


class TestFormatSigned(unittest.TestCase):
    def test_no_percent_in_score_strings(self):
        for n in (-999, -10, -5, -1, 0, 1, 5, 10, 999):
            s = helper.format_signed(n)
            self.assertNotIn("%", s, msg=f"score string must not use percent: {s!r}")

    def test_positive_prefix(self):
        self.assertEqual(helper.format_signed(5), "`+5`")
        self.assertEqual(helper.format_signed(10), "`+10`")

    def test_negative_uses_unicode_minus(self):
        s = helper.format_signed(-5)
        self.assertEqual(s, "`\u22125`")
        self.assertNotIn("-5", s)  # hyphen-minus form avoided inside backticks value

    def test_zero(self):
        self.assertEqual(helper.format_signed(0), "`0`")


class TestCreateSignedIconBar(unittest.TestCase):
    POS = "P"
    NEG = "N"
    ZERO = "Z"
    EMPTY = "."
    BAR = 10

    def _bar(self, score):
        return helper.create_signed_icon_bar(
            score,
            bar_length=self.BAR,
            pos_icon=self.POS,
            neg_icon=self.NEG,
            zero_icon=self.ZERO,
            empty=self.EMPTY,
        )

    def test_representative_redflag_range_fill_and_polarity(self):
        """score S -> exactly min(abs(S), bar) polarity icons."""
        cases = [-10, -5, -1, 0, 1, 5, 10]
        for score in cases:
            bar = self._bar(score)
            expected_fill = min(abs(score), self.BAR) if score != 0 else 0
            if score > 0:
                self.assertEqual(
                    helper.count_polarity_icons(bar, self.POS),
                    expected_fill,
                    msg=f"+score {score} bar={bar!r}",
                )
                self.assertEqual(bar.count(self.NEG), 0)
            elif score < 0:
                self.assertEqual(
                    helper.count_polarity_icons(bar, self.NEG),
                    expected_fill,
                    msg=f"-score {score} bar={bar!r}",
                )
                self.assertEqual(bar.count(self.POS), 0)
            else:
                self.assertEqual(bar, self.ZERO * self.BAR)

    def test_score_five_is_exactly_five_icons(self):
        bar = self._bar(5)
        self.assertEqual(helper.count_polarity_icons(bar, self.POS), 5)
        self.assertEqual(bar, "PPPPP.....")

    def test_score_minus_three_is_exactly_three_neg_icons(self):
        bar = self._bar(-3)
        self.assertEqual(helper.count_polarity_icons(bar, self.NEG), 3)
        self.assertEqual(bar, "NNN.......")

    def test_not_percent_style_mapping(self):
        """
        Percent-style would map small scores poorly on large ranges
        (e.g. 5/999*10 ~= 0). Count-match must show 5 icons for score 5.
        """
        bar = helper.create_signed_icon_bar(
            5,
            bar_length=10,
            pos_icon="✨",
            neg_icon="🌑",
            zero_icon="⚪",
            empty="·",
        )
        self.assertEqual(helper.count_polarity_icons(bar, "✨"), 5)

    def test_cap_at_bar_length_for_large_aura_scores(self):
        bar = self._bar(500)
        self.assertEqual(helper.count_polarity_icons(bar, self.POS), self.BAR)
        bar_neg = self._bar(-999)
        self.assertEqual(helper.count_polarity_icons(bar_neg, self.NEG), self.BAR)

    def test_aura_samples_in_range(self):
        for score in (-999, -500, -10, -1, 0, 1, 10, 42, 500, 999):
            bar = helper.create_signed_icon_bar(
                score,
                bar_length=10,
                pos_icon="✨",
                neg_icon="🌑",
                zero_icon="⚪",
                empty="·",
            )
            expected = 0 if score == 0 else min(abs(score), 10)
            if score > 0:
                self.assertEqual(helper.count_polarity_icons(bar, "✨"), expected)
            elif score < 0:
                self.assertEqual(helper.count_polarity_icons(bar, "🌑"), expected)
            else:
                self.assertEqual(bar, "⚪" * 10)


class TestShippedWrappersUseCountMatch(unittest.TestCase):
    """Structural + behavioral checks against shipped aura/redflag wrappers."""

    def test_aura_and_redflag_source_no_percent_primary_score(self):
        aura_src = AURA_PATH.read_text(encoding="utf-8")
        red_src = REDFLAG_PATH.read_text(encoding="utf-8")
        # Must not use progress bar percent helper for primary display
        self.assertNotIn("create_progress_bar", aura_src)
        self.assertNotIn("create_progress_bar", red_src)
        # Must use shared count-matching bar
        self.assertIn("create_signed_icon_bar", aura_src)
        self.assertIn("create_signed_icon_bar", red_src)
        self.assertIn("format_signed", aura_src)
        self.assertIn("format_signed", red_src)
        # Dropped percent-style scale (filled = abs/max * bar_length)
        self.assertNotIn("abs(aura_points)", aura_src)
        self.assertNotIn(" / 999", aura_src)
        self.assertNotIn("* bar_length", aura_src)

    def test_wrapper_fill_via_shipped_functions(self):
        # Load wrappers: they import discord — skip if unavailable
        try:
            import discord  # noqa: F401
        except ImportError:
            self.skipTest("discord not installed; pure helper tests still cover fill logic")

        # Ensure package path
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))

        from cogs.funny_things.meters.aura import create_aura_icon_bar, AURA_ICON_POS, AURA_ICON_NEG
        from cogs.funny_things.meters.redflag import (
            create_flag_icon_bar,
            RED_FLAG_ICON,
            GREEN_FLAG_ICON,
        )

        self.assertEqual(helper.count_polarity_icons(create_aura_icon_bar(5), AURA_ICON_POS), 5)
        self.assertEqual(helper.count_polarity_icons(create_aura_icon_bar(-3), AURA_ICON_NEG), 3)
        self.assertEqual(helper.count_polarity_icons(create_flag_icon_bar(5), RED_FLAG_ICON), 5)
        self.assertEqual(helper.count_polarity_icons(create_flag_icon_bar(-3), GREEN_FLAG_ICON), 3)


if __name__ == "__main__":
    unittest.main()
