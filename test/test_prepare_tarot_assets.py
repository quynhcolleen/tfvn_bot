import io
import unittest

from PIL import Image, ImageDraw

from scripts.prepare_tarot_assets import (
    CARD_MAX_WIDTH,
    catalog,
    crop_scan_margins,
    encode_card_webp,
    expected_filenames,
)

CREAM = (232, 216, 202)
FRAME = (22, 18, 20)
INNER = (178, 36, 40)


def make_scan(
    width: int = 220,
    height: int = 360,
    *,
    margin: int = 28,
) -> Image.Image:
    image = Image.new("RGB", (width, height), CREAM)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, 2), fill=(250, 250, 248))
    draw.rectangle((width - 3, 0, width - 1, height - 1), fill=(250, 250, 248))
    box = (margin, margin + 2, width - margin - 1, height - margin - 6)
    draw.rectangle(box, outline=FRAME, width=5, fill=INNER)
    return image


class TestPrepareTarotAssets(unittest.TestCase):
    def test_catalog_covers_back_majors_and_four_suits(self) -> None:
        entries = catalog()
        ids = [entry["id"] for entry in entries]
        files = expected_filenames()

        self.assertEqual(len(entries), 79)
        self.assertEqual(len(set(ids)), 79)
        self.assertEqual(len(set(files)), 79)
        self.assertEqual(ids[0], "back")
        self.assertEqual(files[0], "back.webp")
        self.assertEqual(ids[1], "major:00")
        self.assertEqual(ids[22], "major:21")
        self.assertIn("wands:01", ids)
        self.assertIn("cups:14", ids)
        self.assertIn("swords:10", ids)
        self.assertIn("pentacles:14", ids)
        self.assertTrue(all(name.endswith(".webp") for name in files))

    def test_encode_card_webp_caps_width(self) -> None:
        source = Image.new("RGB", (1200, 2000), "navy")
        buffer = io.BytesIO()
        source.save(buffer, format="JPEG", quality=90)
        encoded = encode_card_webp(buffer.getvalue())
        image = Image.open(io.BytesIO(encoded))
        image.load()
        self.assertEqual(image.format, "WEBP")
        self.assertEqual(image.size[0], CARD_MAX_WIDTH)
        self.assertGreater(image.size[1], CARD_MAX_WIDTH)

    def test_crop_scan_margins_trims_cream_paper(self) -> None:
        source = make_scan()
        cropped = crop_scan_margins(source)
        self.assertLess(cropped.size[0], source.size[0] - 30)
        self.assertLess(cropped.size[1], source.size[1] - 30)
        self.assertGreaterEqual(cropped.size[0], 140)
        self.assertGreaterEqual(cropped.size[1], 250)
        self.assertEqual(cropped.getpixel((cropped.size[0] // 2, cropped.size[1] // 2)), INNER)
        again = crop_scan_margins(cropped)
        self.assertEqual(again.size, cropped.size)

    def test_crop_scan_margins_leaves_solid_art_alone(self) -> None:
        source = Image.new("RGB", (200, 340), (24, 22, 90))
        self.assertEqual(crop_scan_margins(source).size, source.size)

    def test_encode_card_webp_crops_then_caps_width(self) -> None:
        source = make_scan(900, 1500, margin=90)
        buffer = io.BytesIO()
        source.save(buffer, format="JPEG", quality=90)
        encoded = encode_card_webp(buffer.getvalue())
        image = Image.open(io.BytesIO(encoded))
        image.load()
        self.assertEqual(image.format, "WEBP")
        self.assertEqual(image.size[0], CARD_MAX_WIDTH)
        sample = image.getpixel((12, image.size[1] // 2))
        cream_distance = sum(abs(sample[i] - CREAM[i]) for i in range(3))
        self.assertGreater(cream_distance, 80)
