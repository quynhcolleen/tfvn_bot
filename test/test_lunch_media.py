import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from cogs.utils._lunch_helpers import load_foods
from cogs.utils._lunch_media import LunchMedia, food_sheet


ROOT = Path(__file__).resolve().parents[1]


class TestLunchMedia(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.asset_dir = Path(directory.name)
        self.sources = {tier: {"duration_seconds": 0.3} for tier in ("blue", "purple", "gold")}
        self.write_sources()
        for tier in self.sources:
            first = Image.new("RGB", (8, 8), "blue")
            second = Image.new("RGB", (8, 8), "gold")
            first.save(
                self.asset_dir / f"wish_{tier}.gif", save_all=True,
                append_images=[second], duration=[100, 200], loop=0,
            )

    def write_sources(self):
        (self.asset_dir / "wish_sources.json").write_text(
            json.dumps(self.sources), encoding="utf-8",
        )

    def media(self):
        media = LunchMedia(self.asset_dir)
        self.addCleanup(media.clear_cache)
        return media

    def sheet(self, name: str, size: tuple[int, int] = (400, 300)):
        image = Image.new("RGB", size)
        # Row/column coordinates encode the source position in lossless pixels.
        image.putdata([(x % 256, y % 256, 0) for y in range(size[1]) for x in range(size[0])])
        image.save(self.asset_dir / name, format="WEBP", lossless=True)

    def test_atlas_group_boundaries_match_source_image_ids(self):
        cases = {
            0: ("food-hd-0.webp", 0, 2, 2),
            3: ("food-hd-0.webp", 3, 2, 2),
            4: ("food-hd-1.webp", 0, 2, 2),
            35: ("food-hd-8.webp", 3, 2, 2),
            36: ("food-expanded-0.webp", 0, 4, 3),
            47: ("food-expanded-0.webp", 11, 4, 3),
            48: ("food-expanded-1.webp", 0, 4, 3),
            71: ("food-expanded-2.webp", 11, 4, 3),
            72: ("food-lunch-0.webp", 0, 4, 3),
            119: ("food-lunch-3.webp", 11, 4, 3),
            120: ("food-common-0.webp", 0, 4, 3),
            129: ("food-common-0.webp", 9, 4, 3),
            1000: ("food-extra-0.webp", 0, 4, 3),
            1011: ("food-extra-0.webp", 11, 4, 3),
            1012: ("food-extra-1.webp", 0, 4, 3),
            1023: ("food-extra-1.webp", 11, 4, 3),
        }
        for image_id, expected in cases.items():
            with self.subTest(image_id=image_id):
                self.assertEqual(food_sheet(image_id), expected)

    def test_animation_manifest_loads_local_paths_and_durations(self):
        media = self.media()
        for tier in self.sources:
            animation = media.wish(tier)
            self.assertEqual(animation.path, self.asset_dir / f"wish_{tier}.gif")
            self.assertEqual(animation.duration_seconds, 0.3)

    def test_bundled_wishes_match_recorded_files_and_playback_durations(self):
        asset_dir = ROOT / "assets" / "lunch"
        media = LunchMedia(asset_dir)
        self.addCleanup(media.clear_cache)
        sources = json.loads((asset_dir / "wish_sources.json").read_text(encoding="utf-8"))
        for tier, source in sources.items():
            with self.subTest(tier=tier):
                animation = media.wish(tier)
                self.assertEqual(animation.path.name, source["file"])
                self.assertEqual(
                    hashlib.sha256(animation.path.read_bytes()).hexdigest(), source["sha256"],
                )
                with Image.open(animation.path) as gif:
                    self.assertTrue(gif.is_animated)
                    duration = 0
                    for frame in range(gif.n_frames):
                        gif.seek(frame)
                        gif.load()
                        duration += gif.info["duration"]
                    self.assertAlmostEqual(duration / 1000, animation.duration_seconds)

    def test_missing_animation_or_manifest_fails_with_clear_context(self):
        for filename in ("wish_blue.gif", "wish_sources.json"):
            with self.subTest(filename=filename):
                path = self.asset_dir / filename
                payload = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "bundled lunch animations"):
                    LunchMedia(self.asset_dir)
                path.write_bytes(payload)

    def test_invalid_animation_duration_and_static_gif_are_rejected(self):
        for duration in (0, -1, 31, "nan", "inf", None):
            with self.subTest(duration=duration):
                self.sources["blue"]["duration_seconds"] = duration
                self.write_sources()
                with self.assertRaisesRegex(ValueError, "bundled lunch animations"):
                    LunchMedia(self.asset_dir)
        self.sources["blue"]["duration_seconds"] = 0.3
        self.write_sources()
        Image.new("RGB", (8, 8), "blue").save(self.asset_dir / "wish_blue.gif")
        with self.assertRaisesRegex(ValueError, "animated GIF"):
            LunchMedia(self.asset_dir)

    def test_food_validation_rejects_missing_or_wrong_format_sheet(self):
        media = self.media()
        with self.assertRaisesRegex(ValueError, "bundled lunch food images"):
            media.validate_foods((0,))
        path = self.asset_dir / "food-hd-0.webp"
        Image.new("RGB", (20, 20)).save(path, format="PNG")
        with self.assertRaisesRegex(ValueError, "WebP"):
            media.validate_foods((0,))
        self.sheet(path.name, (200, 200))
        media.validate_foods((0, 1, 2, 3))

    def test_crops_match_css_row_offsets_and_lower_edge_trims(self):
        self.sheet("food-hd-0.webp", (200, 200))
        self.sheet("food-expanded-0.webp")
        self.sheet("food-lunch-0.webp")
        self.sheet("food-common-0.webp")
        self.sheet("food-extra-0.webp")
        media = self.media()
        cases = (
            (0, (0, 0), (512, 512)),
            (3, (100, 100), (512, 512)),
            (37, (100, 0), (512, 512)),
            (40, (0, 92), (512, 512)),
            (44, (0, 184), (512, 512)),
            (76, (0, 92), (512, 476)),
            (80, (0, 184), (512, 476)),
            (123, (44, 0), (512, 492)),
            (124, (0, 100), (512, 492)),
            (128, (0, 200), (512, 492)),
            (1000, (0, 0), (512, 481)),
            (1005, (100, 92), (512, 481)),
            (1011, (44, 184), (512, 481)),
        )
        for image_id, source_origin, size in cases:
            with self.subTest(image_id=image_id):
                with Image.open(io.BytesIO(media.food_png(image_id))) as crop:
                    self.assertEqual(crop.format, "PNG")
                    self.assertEqual(crop.size, size)
                    self.assertEqual(crop.getpixel((0, 0))[:2], source_origin)

    def test_png_cache_reuses_crop_until_cleared(self):
        self.sheet("food-hd-0.webp", (200, 200))
        media = self.media()
        first = media.food_png(0)
        Image.new("RGB", (200, 200), "red").save(
            self.asset_dir / "food-hd-0.webp", format="WEBP", lossless=True,
        )
        self.assertEqual(media.food_png(0), first)
        media.clear_cache()
        self.assertNotEqual(media.food_png(0), first)

    def test_every_bundled_food_has_a_decodable_image(self):
        # Use the synthetic wishes to exercise real food sheets independently
        # of separately maintained animation sources.
        media = self.media()
        media.asset_dir = ROOT / "assets" / "lunch"
        foods = load_foods(ROOT / "data" / "lunch_foods.json")
        media.validate_foods(tuple(food.image for food in foods))
        for food in foods:
            with self.subTest(image_id=food.image, name=food.name):
                with Image.open(io.BytesIO(media.food_png(food.image))) as picture:
                    self.assertEqual(picture.format, "PNG")
                    self.assertEqual(picture.width, 512)
                    picture.verify()


if __name__ == "__main__":
    unittest.main()
