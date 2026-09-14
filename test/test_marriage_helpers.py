import unittest
from datetime import datetime, timedelta, timezone

from cogs.interaction._marriage_helpers import (
    XP_PER_INTERACTION,
    XP_PER_LEVEL,
    days_together,
    format_marriage_card_value,
    is_pair,
    level_from_xp,
    level_progress_bar,
    marriage_card_info,
    next_rank,
    normalize_pair,
    partner_id_of,
    progress_bar,
    rank_from_level,
    rank_from_xp,
    rank_level_progress_bar,
    xp_progress_in_level,
)


class TestMarriageHelpers(unittest.TestCase):
    def test_normalize_pair_orders_ids(self) -> None:
        self.assertEqual(normalize_pair(5, 2), (2, 5))
        self.assertEqual(normalize_pair(2, 5), (2, 5))

    def test_normalize_pair_rejects_same_user(self) -> None:
        with self.assertRaises(ValueError):
            normalize_pair(1, 1)

    def test_is_pair(self) -> None:
        self.assertTrue(is_pair(10, 20, 10, 20))
        self.assertTrue(is_pair(20, 10, 10, 20))
        self.assertFalse(is_pair(10, 20, 10, 21))
        self.assertFalse(is_pair(1, 1, 1, 2))

    def test_level_from_xp_easy_curve(self) -> None:
        self.assertEqual(level_from_xp(0), 1)
        self.assertEqual(level_from_xp(19), 1)
        self.assertEqual(level_from_xp(20), 2)
        self.assertEqual(level_from_xp(39), 2)
        # Four interactions at +5 XP each → level 2
        self.assertEqual(level_from_xp(4 * XP_PER_INTERACTION), 2)

    def test_level_from_xp_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            level_from_xp(-1)

    def test_rank_ladder(self) -> None:
        self.assertEqual(rank_from_level(1).key, "bronze")
        self.assertEqual(rank_from_level(4).key, "bronze")
        self.assertEqual(rank_from_level(5).key, "silver")
        self.assertEqual(rank_from_level(10).key, "gold")
        self.assertEqual(rank_from_level(18).key, "diamond")
        self.assertEqual(rank_from_level(28).key, "blue_sapphire")
        self.assertEqual(rank_from_level(150).key, "eternal")
        self.assertEqual(rank_from_level(999).key, "eternal")

    def test_rank_from_xp_matches_level(self) -> None:
        # Silver starts at level 5 → xp >= 80
        self.assertEqual(rank_from_xp(80).key, "silver")
        self.assertEqual(rank_from_xp(79).key, "bronze")

    def test_next_rank(self) -> None:
        self.assertEqual(next_rank(1).key, "silver")
        self.assertEqual(next_rank(5).key, "gold")
        self.assertIsNone(next_rank(150))

    def test_xp_progress_in_level(self) -> None:
        self.assertEqual(xp_progress_in_level(0), (0, XP_PER_LEVEL))
        self.assertEqual(xp_progress_in_level(19), (19, XP_PER_LEVEL))
        self.assertEqual(xp_progress_in_level(20), (0, XP_PER_LEVEL))

    def test_progress_bar(self) -> None:
        self.assertEqual(progress_bar(0.0, 10), "░" * 10)
        self.assertEqual(progress_bar(1.0, 10), "█" * 10)
        self.assertEqual(len(level_progress_bar(10)), 10)
        self.assertEqual(len(rank_level_progress_bar(3)), 10)

    def test_days_together(self) -> None:
        now = datetime(2026, 3, 16, tzinfo=timezone.utc)
        married = now - timedelta(days=3, hours=5)
        self.assertEqual(days_together(married, now), 3)

    def test_partner_id_of(self) -> None:
        marriage = {"user_a": 10, "user_b": 20}
        self.assertEqual(partner_id_of(marriage, 10), 20)
        self.assertEqual(partner_id_of(marriage, 20), 10)

    def test_partner_id_of_rejects_outsider(self) -> None:
        with self.assertRaises(ValueError):
            partner_id_of({"user_a": 10, "user_b": 20}, 30)

    def test_format_unmarried_card_value(self) -> None:
        now = datetime(2026, 9, 14, tzinfo=timezone.utc)
        self.assertEqual(
            format_marriage_card_value(None, 10, now=now),
            "Độc thân ✨",
        )

    def test_format_married_card_value(self) -> None:
        now = datetime(2026, 9, 14, tzinfo=timezone.utc)
        marriage = {
            "user_a": 10,
            "user_b": 20,
            "xp": 80,
            "level": 5,
            "married_at": datetime(2026, 3, 14),
        }
        info = marriage_card_info(marriage, 10, now=now)
        self.assertEqual(info.partner_id, 20)
        self.assertEqual(info.rank.key, "silver")
        self.assertEqual(info.level, 5)
        self.assertEqual(info.married_on, "2026-03-14")
        self.assertEqual(info.days_together, 184)

        text = format_marriage_card_value(marriage, 10, now=now)
        self.assertIn("<@20>", text)
        self.assertIn("Bạc", text)
        self.assertIn("Level **5**", text)
        self.assertIn("2026-03-14", text)
        self.assertIn("**184 ngày** bên nhau", text)


if __name__ == "__main__":
    unittest.main()
