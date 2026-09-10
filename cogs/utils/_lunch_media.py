"""Offline food-sheet crops and Genshin wish animations for lunch suggestions."""

from dataclasses import dataclass
from functools import lru_cache
import io
import json
import math
from pathlib import Path

from PIL import Image


ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "lunch"


@dataclass(frozen=True)
class WishAnimation:
    path: Path
    duration_seconds: float


def food_sheet(image_id: int) -> tuple[str, int, int, int]:
    """Resolve local additions or the upstream FoodImage atlas coordinates."""
    if image_id >= 1000:
        offset = image_id - 1000
        return f"food-extra-{offset // 12}.webp", offset % 12, 4, 3
    if image_id >= 120:
        offset, group = image_id - 120, "common"
    elif image_id >= 72:
        offset, group = image_id - 72, "lunch"
    elif image_id >= 36:
        offset, group = image_id - 36, "expanded"
    else:
        return f"food-hd-{image_id // 4}.webp", image_id % 4, 2, 2
    return f"food-{group}-{offset // 12}.webp", offset % 12, 4, 3


class LunchMedia:
    def __init__(self, asset_dir: Path = ASSET_DIR) -> None:
        self.asset_dir = asset_dir
        self._wishes: dict[str, WishAnimation] = {}
        try:
            sources = json.loads((asset_dir / "wish_sources.json").read_text(encoding="utf-8"))
            for tier in ("blue", "purple", "gold"):
                entry = sources[tier]
                path = asset_dir / f"wish_{tier}.gif"
                duration = float(entry["duration_seconds"])
                if not math.isfinite(duration) or not 0 < duration <= 30:
                    raise ValueError(f"Invalid {tier} wish duration")
                with Image.open(path) as animation:
                    if animation.format != "GIF" or not animation.is_animated:
                        raise ValueError(f"Expected an animated GIF: {path.name}")
                    animation.verify()
                self._wishes[tier] = WishAnimation(path, duration)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Could not load bundled lunch animations: {error}") from error

    def validate_foods(self, image_ids: tuple[int, ...]) -> None:
        """Fail cog loading clearly if any required food sheet is unavailable."""
        try:
            for filename in {food_sheet(image_id)[0] for image_id in image_ids}:
                with Image.open(self.asset_dir / filename) as sheet:
                    if sheet.format != "WEBP":
                        raise ValueError(f"Expected a WebP food sheet: {filename}")
                    sheet.verify()
        except (OSError, ValueError) as error:
            raise ValueError(f"Could not load bundled lunch food images: {error}") from error

    def wish(self, tier: str) -> WishAnimation:
        return self._wishes[tier]

    @lru_cache(maxsize=256)
    def food_png(self, image_id: int) -> bytes:
        """Crop a dish using the original web UI's offsets and lower-edge trims.

        CSS background-position percentages are measured over the sheet minus
        the visible cell. Expanded/lunch and local rows use 0/46/92%, while
        common rows use 0/50/100%; an ordinary grid crop would show adjacent art.
        """
        filename, index, columns, rows = food_sheet(image_id)
        with Image.open(self.asset_dir / filename) as sheet:
            width, height = sheet.size
            cell_width, cell_height = width / columns, height / rows
            column, row = index % columns, index // columns
            x = column * cell_width
            if columns == 2:
                y = row * cell_height
            else:
                positions = (0, .5, 1) if 120 <= image_id < 1000 else (0, .46, .92)
                y = (height - cell_height) * positions[row]
            trim = .06 if image_id >= 1000 else .04 if image_id >= 120 else .07 if image_id >= 72 else 0
            crop = sheet.crop((round(x), round(y), round(x + cell_width),
                               round(y + cell_height * (1 - trim))))
            # The original winner art uses a square background, including sheets
            # whose individual source cells are not square.
            crop = crop.resize((512, round(512 * (1 - trim))), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            crop.save(output, format="PNG")
            return output.getvalue()

    def clear_cache(self) -> None:
        self.food_png.cache_clear()
