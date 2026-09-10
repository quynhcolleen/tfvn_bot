"""Refresh the bundled lunch catalog and food sheets from a pinned revision.

Run from the repository root with the project's virtual environment:
    python scripts/prepare_lunch_assets.py
    python scripts/prepare_lunch_assets.py --offline
Local additions in data/lunch_foods_extra.json and their food-extra sheets are
preserved during upstream refreshes. Offline mode only rebuilds the catalog.
Genshin wish animations are maintained separately in assets/lunch/wish_sources.json.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata
from urllib.request import urlopen

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVISION = "87c41e2a4eb00834666f2970b278e7ceeaa4886a"
UPSTREAM = "https://github.com/truanayangi-com/truanayangi"
LOCAL_IMAGE_START = 1000


def sheet_name(image_id: int) -> str:
    """Match upstream FoodImage groups and the separate local 4-by-3 sheets."""
    if image_id >= LOCAL_IMAGE_START:
        return f"food-extra-{(image_id - LOCAL_IMAGE_START) // 12}.webp"
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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )


def read_foods(path: Path, *, optional: bool = False) -> list[dict[str, object]]:
    """Read a catalog source, allowing only an absent optional extras file."""
    if optional and not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Lunch foods in {path} must be a JSON array")
    if any(not isinstance(record, dict) for record in records):
        raise ValueError(f"Lunch foods in {path} must contain JSON objects")
    return records


def merge_foods(
    upstream: list[dict[str, object]], extras: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Validate sources and retain their order while protecting local image IDs."""
    if not isinstance(upstream, list) or not isinstance(extras, list):
        raise ValueError("Lunch food sources must be JSON arrays")
    if not upstream and not extras:
        raise ValueError("The combined lunch catalog must not be empty")
    images: set[int] = set()
    names: set[str] = set()
    for label, records in (("Upstream", upstream), ("Local", extras)):
        for index, record in enumerate(records, start=1):
            location = f"{label} lunch food {index}"
            if not isinstance(record, dict):
                raise ValueError(f"{location} must be a JSON object")
            image_id = record.get("image")
            if type(image_id) is not int or image_id < 0:
                raise ValueError(f"{location} needs a nonnegative integer image ID")
            if (image_id >= LOCAL_IMAGE_START) != (label == "Local"):
                raise ValueError(f"{location} uses an image ID reserved for the other source")
            if image_id in images:
                raise ValueError(f"{location} has duplicate image ID {image_id}")
            for field in ("name", "sub"):
                value = record.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{location} needs nonempty text for {field}")
            name = unicodedata.normalize("NFC", " ".join(record["name"].split())).casefold()
            if name in names:
                raise ValueError(f"{location} has duplicate name {record['name']!r}")
            price = record.get("price")
            if type(price) is not int or price <= 0:
                raise ValueError(f"{location} needs a positive integer price")
            if not isinstance(record.get("veg", False), bool):
                raise ValueError(f"{location} needs a boolean veg flag")
            if not isinstance(record.get("quip", ""), str):
                raise ValueError(f"{location} needs text for quip")
            images.add(image_id)
            names.add(name)
    return upstream + extras


def validate_sheet(payload: bytes, name: str) -> None:
    """Reject invalid image payloads before changing the bundled catalog."""
    with Image.open(io.BytesIO(payload)) as picture:
        if picture.format != "WEBP":
            raise ValueError(f"Expected WebP food sheet: {name}")
        picture.verify()


def validate_bundled_sheets(foods: list[dict[str, object]], asset_dir: Path) -> None:
    """Check all existing sheets needed by a set of validated food records."""
    for name in sorted({sheet_name(food["image"]) for food in foods}):
        validate_sheet((asset_dir / name).read_bytes(), name)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument(
        "--offline", action="store_true",
        help="Merge local additions into the bundled catalog without network access",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be a full lowercase Git commit SHA")
    asset_dir = ROOT / "assets" / "lunch"
    catalog_path = ROOT / "data" / "lunch_foods.json"
    extras = read_foods(ROOT / "data" / "lunch_foods_extra.json", optional=True)
    if args.offline:
        bundled = read_foods(catalog_path)
        if any(type(row.get("image")) is not int or row["image"] < 0 for row in bundled):
            raise ValueError("Bundled image identifiers must be nonnegative integers")
        upstream = [row for row in bundled if row["image"] < LOCAL_IMAGE_START]
        foods = merge_foods(upstream, extras)
        validate_bundled_sheets(foods, asset_dir)
        write_json(catalog_path, foods)
        print(f"Prepared {len(foods)} foods offline ({len(extras)} local additions).")
        return

    base_url = f"https://raw.githubusercontent.com/truanayangi-com/truanayangi/{args.revision}"
    source = fetch(f"{base_url}/src/lib/foods.ts").decode("utf-8")
    match = re.search(r"export const foods\s*:\s*Food\[\]\s*=\s*(\[.*\])\s*\.map", source, re.S)
    if match is None:
        raise ValueError("Upstream foods.ts format changed; review before refreshing")
    rows = json.loads(match.group(1))
    upstream = [
        {key: row.get(key, False if key == "veg" else "")
         for key in ("image", "name", "sub", "price", "veg", "quip")}
        for row in rows
    ]
    if not upstream:
        raise ValueError("Upstream lunch catalog must not be empty")
    foods = merge_foods(upstream, extras)
    image_ids = [food["image"] for food in upstream]
    validate_bundled_sheets(extras, asset_dir)

    # Download and validate everything before changing the bundled copy.
    sheets = {}
    for name in sorted({sheet_name(image_id) for image_id in image_ids}):
        payload = fetch(f"{base_url}/public/{name}")
        validate_sheet(payload, name)
        sheets[name] = payload
        print(f"Validated {name}: {len(payload):,} bytes")

    asset_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in sheets.items():
        (asset_dir / name).write_bytes(payload)
    write_json(catalog_path, foods)
    write_json(asset_dir / "food_sources.json", {
        "repository": UPSTREAM,
        "revision": args.revision,
        "catalog": f"{UPSTREAM}/blob/{args.revision}/src/lib/foods.ts",
        "image_mapping": f"{UPSTREAM}/blob/{args.revision}/src/app/page.tsx",
        "sheets": {name: {"url": f"{base_url}/public/{name}",
                          "sha256": hashlib.sha256(payload).hexdigest()}
                   for name, payload in sheets.items()},
    })
    print(f"Prepared {len(foods)} foods ({len(extras)} local additions) "
          f"and refreshed {len(sheets)} upstream food sheets.")


if __name__ == "__main__":
    main()
