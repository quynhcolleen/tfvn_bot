import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cogs.utils._lunch_helpers import (
    Food,
    LunchFilters,
    choose_food,
    eligible_foods,
    format_price,
    load_foods,
    parse_budget,
    parse_lunch_filters,
    wish_tier,
)


class TestLunchParsing(unittest.TestCase):
    def test_budget_uses_thousands_and_accepts_suffix(self):
        for value in ("50", "50k", "50K", " 50k ", "00050"):
            with self.subTest(value=value):
                self.assertEqual(parse_budget(value), 50)
        self.assertEqual(parse_budget("999999999k"), 999999999)

    def test_budget_rejects_invalid_and_excessively_long_input(self):
        for value in (
            "", "0", "0k", "-5", "+5", "50.5", "50,000", "50 000",
            "50kk", "k50", "true", "False", "５０", "1000000000", "1e3",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "50k"):
                parse_budget(value)

    def test_empty_budget_and_vegetarian_only_filters(self):
        self.assertEqual(parse_lunch_filters(" \t\n"), LunchFilters())
        self.assertEqual(parse_lunch_filters("50K"), LunchFilters(budget=50))
        self.assertEqual(
            parse_lunch_filters("ChAy"), LunchFilters(vegetarian=True),
        )

    def test_combined_filters_in_either_order(self):
        for value in ("50 chay", "ChAy 50k", " 50K\tCHAY "):
            with self.subTest(value=value):
                self.assertEqual(parse_lunch_filters(value), LunchFilters(50, True))

    def test_duplicates_unknown_and_invalid_budget_are_rejected(self):
        for value in (
            "50 60", "chay CHAY", "50 chay 50", "meat", "50 vegetarian",
            "0", "0 chay", "-1", "50.5 chay", "50 k", "true",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "lunch"):
                parse_lunch_filters(value)


class TestLunchSelection(unittest.TestCase):
    def setUp(self):
        self.foods = (
            Food(0, "Phở bò", "Bò và bánh phở", 50),
            Food(1, "Cơm chay", "Rau và đậu", 50, veg=True),
            Food(2, "Bún chay", "Bún và rau", 51, veg=True),
            Food(3, "Bánh mì", "Bánh mì thịt", 25),
        )

    def test_unfiltered_returns_all(self):
        self.assertEqual(eligible_foods(self.foods, LunchFilters()), self.foods)

    def test_budget_includes_boundary(self):
        self.assertEqual(
            eligible_foods(self.foods, LunchFilters(budget=50)),
            (self.foods[0], self.foods[1], self.foods[3]),
        )

    def test_vegetarian_requires_explicit_flag(self):
        self.assertEqual(
            eligible_foods(self.foods, LunchFilters(vegetarian=True)),
            (self.foods[1], self.foods[2]),
        )

    def test_filters_apply_together(self):
        self.assertEqual(
            eligible_foods(self.foods, LunchFilters(50, True)), (self.foods[1],),
        )

    def test_no_matches(self):
        self.assertEqual(eligible_foods(self.foods, LunchFilters(24)), ())
        self.assertEqual(eligible_foods((), LunchFilters()), ())

    def test_uniform_choice_receives_every_candidate_once(self):
        for selected in self.foods:
            with self.subTest(selected=selected.image):
                with patch(
                    "cogs.utils._lunch_helpers.random.choice", return_value=selected,
                ) as choice:
                    self.assertEqual(choose_food(self.foods), selected)
                choice.assert_called_once_with(self.foods)

    def test_reroll_excludes_previous_without_excluding_other_candidates(self):
        with patch(
            "cogs.utils._lunch_helpers.random.choice", return_value=self.foods[2],
        ) as choice:
            self.assertEqual(choose_food(self.foods, previous_image=1), self.foods[2])
        choice.assert_called_once_with((self.foods[0], self.foods[2], self.foods[3]))

    def test_singleton_can_be_chosen_again(self):
        self.assertEqual(choose_food((self.foods[0],), previous_image=0), self.foods[0])

    def test_empty_selection_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "empty selection"):
            choose_food(())

    def test_rarity_boundary_and_vietnamese_price(self):
        for price, tier in ((1, "blue"), (65, "blue"), (66, "purple"),
                            (130, "purple"), (131, "gold")):
            with self.subTest(price=price):
                self.assertEqual(wish_tier(price), tier)
        self.assertEqual(format_price(50), "50.000₫")
        self.assertEqual(format_price(1250), "1.250.000₫")


class TestLunchDataset(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "foods.json"
        self.record = {
            "image": 0,
            "name": "Cơm chay",
            "sub": "Cơm, rau và đậu hũ",
            "price": 50,
            "veg": True,
            "quip": "Ăn ngon nhé!",
        }

    def write_json(self, value):
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_utf8_and_fields_preserved_with_absolute_path(self):
        self.write_json([self.record])
        self.assertEqual(load_foods(self.path), (Food(**self.record),))
        self.assertNotEqual(self.path.parent, Path.cwd())

    def test_missing_optional_flags_are_safe_defaults(self):
        self.record.pop("veg")
        self.record.pop("quip")
        self.write_json([self.record])
        foods = load_foods(self.path)
        self.assertFalse(foods[0].veg)
        self.assertEqual(foods[0].quip, "")
        self.assertEqual(eligible_foods(foods, LunchFilters(vegetarian=True)), ())

    def test_wrong_top_level_empty_array_and_wrong_record_type(self):
        for value in ({}, None, [], ["dish"]):
            with self.subTest(value=value):
                self.write_json(value)
                with self.assertRaisesRegex(ValueError, "Lunch food"):
                    load_foods(self.path)

    def test_invalid_required_and_optional_field_types(self):
        invalid_values = {
            "image": (None, -1, True, "0", 1.5),
            "name": (None, "", "  ", 42),
            "sub": (None, "", "\t", False),
            "price": (None, 0, -1, True, "50", 50.5),
            "veg": (None, "true", 1),
            "quip": (None, 42, False),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.write_json([{**self.record, field: value}])
                    with self.assertRaisesRegex(ValueError, field):
                        load_foods(self.path)

    def test_required_fields_cannot_be_omitted(self):
        for field in ("image", "name", "sub", "price"):
            with self.subTest(field=field):
                record = {key: value for key, value in self.record.items() if key != field}
                self.write_json([record])
                with self.assertRaisesRegex(ValueError, field):
                    load_foods(self.path)

    def test_duplicate_image_id_is_rejected(self):
        self.write_json([self.record, {**self.record, "name": "Món khác"}])
        with self.assertRaisesRegex(ValueError, "duplicate image ID 0"):
            load_foods(self.path)

    def test_missing_file_invalid_json_and_encoding_have_context(self):
        with self.assertRaisesRegex(ValueError, "Could not load lunch foods"):
            load_foods(self.path)
        self.path.write_text("[", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Could not load lunch foods"):
            load_foods(self.path)
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "Could not load lunch foods"):
            load_foods(self.path)


if __name__ == "__main__":
    unittest.main()
