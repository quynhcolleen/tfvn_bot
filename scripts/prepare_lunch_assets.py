"""Refresh the bundled lunch catalog and food sheets from a pinned revision.

Run from the repository root with the project's virtual environment:
    python scripts/prepare_lunch_assets.py
Genshin wish animations are maintained separately in assets/lunch/wish_sources.json.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.request import urlopen

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVISION = "87c41e2a4eb00834666f2970b278e7ceeaa4886a"
UPSTREAM = "https://github.com/truanayangi-com/truanayangi"


def sheet_name(image_id: int) -> str:
    """Match the FoodImage atlas groups in the upstream page component."""
    if image_id >= 120:
        return f"food-common-{(image_id - 120) // 12}.webp"
    if image_id >= 72:
        return f"food-lunch-{(image_id - 72) // 12}.webp"
    if image_id >= 36:
        return f"food-expanded-{(image_id - 36) // 12}.webp"
    return f"food-hd-{image_id // 4}.webp"


def fetch(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be a full lowercase Git commit SHA")
    base_url = f"https://raw.githubusercontent.com/truanayangi-com/truanayangi/{args.revision}"
    source = fetch(f"{base_url}/src/lib/foods.ts").decode("utf-8")
    match = re.search(r"export const foods\s*:\s*Food\[\]\s*=\s*(\[.*\])\s*\.map", source, re.S)
    if match is None:
        raise ValueError("Upstream foods.ts format changed; review before refreshing")
    rows = json.loads(match.group(1))
    foods = [
        {key: row.get(key, False if key == "veg" else "")
         for key in ("image", "name", "sub", "price", "veg", "quip")}
        for row in rows
    ]
    image_ids = [food["image"] for food in foods]
    if (not foods or any(type(value) is not int or value < 0 for value in image_ids)
            or len(set(image_ids)) != len(image_ids)):
        raise ValueError("Upstream image identifiers are invalid or duplicated")

    # Download and validate everything before changing the bundled copy.
    sheets = {}
    for name in sorted({sheet_name(image_id) for image_id in image_ids}):
        payload = fetch(f"{base_url}/public/{name}")
        with Image.open(io.BytesIO(payload)) as picture:
            if picture.format != "WEBP":
                raise ValueError(f"Expected WebP food sheet: {name}")
            picture.verify()
        sheets[name] = payload
        print(f"Validated {name}: {len(payload):,} bytes")

    asset_dir = ROOT / "assets" / "lunch"
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in sheets.items():
        (asset_dir / name).write_bytes(payload)
    write_json(ROOT / "data" / "lunch_foods.json", foods)
    write_json(asset_dir / "food_sources.json", {
        "repository": UPSTREAM,
        "revision": args.revision,
        "catalog": f"{UPSTREAM}/blob/{args.revision}/src/lib/foods.ts",
        "image_mapping": f"{UPSTREAM}/blob/{args.revision}/src/app/page.tsx",
        "sheets": {name: {"url": f"{base_url}/public/{name}",
                          "sha256": hashlib.sha256(payload).hexdigest()}
                   for name, payload in sheets.items()},
    })
    print(f"Prepared {len(foods)} foods and {len(sheets)} food sheets.")


if __name__ == "__main__":
    main()
