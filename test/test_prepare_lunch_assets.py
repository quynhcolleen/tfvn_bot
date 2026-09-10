import contextlib
import io
import json
from pathlib import Path
import tempfile
import unicodedata
import unittest
from unittest.mock import call, patch

from PIL import Image

from scripts import prepare_lunch_assets as prepare


def food(image: int, name: str = "Cơm gà") -> dict[str, object]:
    return {"image": image, "name": name, "sub": "Một phần ăn trưa", "price": 45,
            "veg": False, "quip": "Ăn ngon nhé!"}


def image_bytes(format: str = "WEBP") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "orange").save(output, format=format)
    return output.getvalue()


class TestMergeLunchFoods(unittest.TestCase):
    def test_merge_preserves_source_records_and_order(self):
        upstream = [food(0), food(120, "Bún bò")]
        extras = [food(1000, "Cơm niêu"), food(1012, "Bún rau")]
        original = json.dumps([upstream, extras], ensure_ascii=False)

        self.assertEqual(prepare.merge_foods(upstream, extras), upstream + extras)
        self.assertEqual(json.dumps([upstream, extras], ensure_ascii=False), original)
        self.assertEqual(prepare.merge_foods(upstream, []), upstream)

    def test_duplicate_identifiers_are_rejected(self):
        for upstream, extras in (
            ([food(0), food(0, "Bún bò")], []),
            ([food(0)], [food(1000, "Cơm niêu"), food(1000, "Bún rau")]),
        ):
            with self.subTest(upstream=upstream, extras=extras):
                with self.assertRaisesRegex(ValueError, "duplicate image ID"):
                    prepare.merge_foods(upstream, extras)

    def test_names_are_unique_across_sources_after_normalization(self):
        for name in ("CƠM GÀ", "  Cơm   gà ", unicodedata.normalize("NFD", "Cơm gà")):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "duplicate name"):
                    prepare.merge_foods([food(0)], [food(1000, name)])

    def test_image_ranges_are_reserved_for_their_source(self):
        for upstream, extras in (([food(1000)], []), ([food(0)], [food(999, "Bún rau")])):
            with self.subTest(upstream=upstream, extras=extras):
                with self.assertRaisesRegex(ValueError, "reserved"):
                    prepare.merge_foods(upstream, extras)

    def test_bad_record_fields_and_empty_sources_are_rejected(self):
        for field, value in (
            ("image", True), ("image", -1), ("image", "0"),
            ("name", " "), ("sub", None), ("price", True),
            ("price", 0), ("price", 1.5), ("veg", "false"), ("quip", None),
        ):
            with self.subTest(field=field, value=value):
                record = food(0)
                record[field] = value
                with self.assertRaises(ValueError):
                    prepare.merge_foods([record], [])
        for upstream, extras in (([], []), ([None], []), ({}, []), ([], {})):
            with self.subTest(upstream=upstream, extras=extras):
                with self.assertRaises(ValueError):
                    prepare.merge_foods(upstream, extras)

    def test_local_sheet_boundaries(self):
        self.assertEqual(prepare.sheet_name(1000), "food-extra-0.webp")
        self.assertEqual(prepare.sheet_name(1011), "food-extra-0.webp")
        self.assertEqual(prepare.sheet_name(1012), "food-extra-1.webp")
        self.assertEqual(prepare.sheet_name(1023), "food-extra-1.webp")


class TestPrepareLunchAssets(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.asset_dir = self.root / "assets" / "lunch"
        self.asset_dir.mkdir(parents=True)
        (self.root / "data").mkdir()
        self.catalog = self.root / "data" / "lunch_foods.json"
        self.extra_path = self.root / "data" / "lunch_foods_extra.json"
        self.provenance = self.asset_dir / "food_sources.json"
        self.upstream = [food(0), food(120, "Bún bò")]
        self.extras = [food(1000, "Cơm niêu"), food(1012, "Bún rau")]
        prepare.write_json(self.catalog, self.upstream + self.extras)
        prepare.write_json(self.extra_path, self.extras)
        prepare.write_json(self.provenance, {"revision": "original provenance"})
        for record in self.upstream + self.extras:
            (self.asset_dir / prepare.sheet_name(record["image"])).write_bytes(image_bytes())
        self.enterContext(patch.object(prepare, "ROOT", self.root))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_offline_regeneration_is_idempotent_without_network_or_provenance_changes(self):
        self.extras[0]["price"] = 60
        prepare.write_json(self.extra_path, self.extras)
        old_provenance = self.provenance.read_bytes()
        old_sheets = {path.name: path.read_bytes() for path in self.asset_dir.glob("*.webp")}
        with patch.object(prepare, "fetch", side_effect=AssertionError("Network requested")):
            prepare.main(["--offline"])
            first_result = self.catalog.read_bytes()
            prepare.main(["--offline"])

        self.assertEqual(self.catalog.read_bytes(), first_result)
        self.assertEqual(json.loads(first_result), self.upstream + self.extras)
        self.assertEqual(self.provenance.read_bytes(), old_provenance)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.asset_dir.glob("*.webp")},
            old_sheets,
        )

    def test_normal_refresh_preserves_local_foods_and_sheets(self):
        refreshed = [dict(self.upstream[0], price=50), self.upstream[1]]
        source = "export const foods: Food[] = " + json.dumps(refreshed) + ".map(food => food);"
        sheet = image_bytes()
        old_local = {path.name: path.read_bytes()
                     for path in self.asset_dir.glob("food-extra-*.webp")}
        with patch.object(prepare, "fetch", side_effect=[source.encode(), sheet, sheet]) as fetch:
            prepare.main([])

        base_url = ("https://raw.githubusercontent.com/truanayangi-com/truanayangi/"
                    + prepare.DEFAULT_REVISION)
        self.assertEqual(fetch.call_args_list, [
            call(f"{base_url}/src/lib/foods.ts"),
            call(f"{base_url}/public/food-common-0.webp"),
            call(f"{base_url}/public/food-hd-0.webp"),
        ])
        self.assertEqual(prepare.read_foods(self.catalog), refreshed + self.extras)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.asset_dir.glob("food-extra-*.webp")},
            old_local,
        )
        self.assertEqual(set(json.loads(self.provenance.read_text())["sheets"]),
                         {"food-common-0.webp", "food-hd-0.webp"})

    def test_missing_optional_extras_file_allows_base_only_catalog(self):
        self.extra_path.unlink()
        with patch.object(prepare, "fetch", side_effect=AssertionError("Network requested")):
            prepare.main(["--offline"])
        self.assertEqual(prepare.read_foods(self.catalog), self.upstream)

    def test_invalid_extra_records_preserve_existing_catalog(self):
        for extras in (
            [food(1000, "CƠM GÀ")],
            [food(1000, "Bún rau"), food(1000, "Cơm niêu")],
            [dict(food(1000, "Cơm niêu"), price=True)],
            {"image": 1000},
            ["Cơm niêu"],
        ):
            with self.subTest(extras=extras):
                prepare.write_json(self.extra_path, extras)
                original = self.catalog.read_bytes()
                with self.assertRaises(ValueError):
                    prepare.main(["--offline"])
                self.assertEqual(self.catalog.read_bytes(), original)

    def test_malformed_json_preserves_existing_catalog(self):
        self.extra_path.write_text("[invalid JSON", encoding="utf-8")
        original = self.catalog.read_bytes()
        with patch.object(prepare, "fetch") as fetch:
            with self.assertRaises(ValueError):
                prepare.main([])
        self.assertEqual(self.catalog.read_bytes(), original)
        fetch.assert_not_called()

    def test_empty_upstream_refresh_does_not_replace_base_with_only_extras(self):
        original = self.catalog.read_bytes()
        source = b"export const foods: Food[] = [].map(f => f);"
        with patch.object(prepare, "fetch", return_value=source) as fetch:
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                prepare.main([])
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(fetch.call_count, 1)

    def test_missing_local_sheet_preserves_catalog_and_provenance(self):
        (self.asset_dir / "food-extra-1.webp").unlink()
        original = self.catalog.read_bytes()
        original_provenance = self.provenance.read_bytes()
        with self.assertRaises(FileNotFoundError):
            prepare.main(["--offline"])
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(self.provenance.read_bytes(), original_provenance)

    def test_non_webp_local_sheet_is_rejected_before_refresh_writes(self):
        (self.asset_dir / "food-extra-0.webp").write_bytes(image_bytes("PNG"))
        original = self.catalog.read_bytes()
        original_provenance = self.provenance.read_bytes()
        source = "export const foods: Food[] = " + json.dumps(self.upstream) + ".map(f => f);"
        with patch.object(prepare, "fetch", return_value=source.encode()) as fetch:
            with self.assertRaisesRegex(ValueError, "Expected WebP"):
                prepare.main([])
        self.assertEqual(self.catalog.read_bytes(), original)
        self.assertEqual(self.provenance.read_bytes(), original_provenance)
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
