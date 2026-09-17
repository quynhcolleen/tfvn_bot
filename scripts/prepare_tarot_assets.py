"""Download public-domain Rider-Waite-Smith scans into assets/tarot/.

The 1909 Pamela Colman Smith / A. E. Waite illustrations are public domain in
the United States. Wikimedia Commons hosts the scans used here. Bundled files
are cropped to the printed frame so cream paper and scanner white do not show
as a thick edge on the spread cloth.

Run from the repository root:

    python scripts/prepare_tarot_assets.py
    python scripts/prepare_tarot_assets.py --from-existing
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets" / "tarot"
SOURCES_PATH = ASSET_DIR / "sources.json"
COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"
USER_AGENT = (
    "tfvn-bot-tarot-assets/1.0 "
    "(https://github.com/quynhcolleen/tfvn_bot; public-domain RWS bundling)"
)
CARD_MAX_WIDTH = 360
WEBP_QUALITY = 82
# Scan margins: cream paper / scanner white vs the printed frame.
CROP_DARK = 82
CROP_PAPER_LUMA = 168
CROP_PAPER_CHROMA = 74
CROP_DARK_FRACTION = 0.16
CROP_PAPER_FRACTION = 0.62
CROP_MAX_SIDE_FRACTION = 0.18
CROP_MIN_KEEP_FRACTION = 0.70

MAJOR_COMMONS = (
    "RWS_Tarot_00_Fool.jpg",
    "RWS_Tarot_01_Magician.jpg",
    "RWS_Tarot_02_High_Priestess.jpg",
    "RWS_Tarot_03_Empress.jpg",
    "RWS_Tarot_04_Emperor.jpg",
    "RWS_Tarot_05_Hierophant.jpg",
    "RWS_Tarot_06_Lovers.jpg",
    "RWS_Tarot_07_Chariot.jpg",
    "RWS_Tarot_08_Strength.jpg",
    "RWS_Tarot_09_Hermit.jpg",
    "RWS_Tarot_10_Wheel_of_Fortune.jpg",
    "RWS_Tarot_11_Justice.jpg",
    "RWS_Tarot_12_Hanged_Man.jpg",
    "RWS_Tarot_13_Death.jpg",
    "RWS_Tarot_14_Temperance.jpg",
    "RWS_Tarot_15_Devil.jpg",
    "RWS_Tarot_16_Tower.jpg",
    "RWS_Tarot_17_Star.jpg",
    "RWS_Tarot_18_Moon.jpg",
    "RWS_Tarot_19_Sun.jpg",
    "RWS_Tarot_20_Judgement.jpg",
    "RWS_Tarot_21_World.jpg",
)

SUIT_COMMONS_PREFIX = {
    "wands": "Wands",
    "cups": "Cups",
    "swords": "Swords",
    "pentacles": "Pents",
}

BACK_COMMONS = "Waite–Smith_Tarot_Roses_and_Lilies_cropped.jpg"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fetch(url: str) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"Download failed ({response.status}): {url}")
        payload = response.read()
        final_url = response.geturl().split("?", 1)[0]
    if not payload:
        raise RuntimeError(f"Empty download: {url}")
    return payload, final_url


def _edge_profiles(image: Image.Image) -> tuple[list[float], list[float], list[float], list[float]]:
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.tobytes()
    row_dark = [0.0] * height
    row_paper = [0.0] * height
    col_dark_n = [0] * width
    col_paper_n = [0] * width
    index = 0
    for y in range(height):
        dark_n = 0
        paper_n = 0
        for x in range(width):
            red = pixels[index]
            green = pixels[index + 1]
            blue = pixels[index + 2]
            index += 3
            luma = (red + green + blue) // 3
            chroma = max(red, green, blue) - min(red, green, blue)
            if luma < CROP_DARK:
                dark_n += 1
                col_dark_n[x] += 1
            if luma >= CROP_PAPER_LUMA and chroma <= CROP_PAPER_CHROMA:
                paper_n += 1
                col_paper_n[x] += 1
        row_dark[y] = dark_n / width
        row_paper[y] = paper_n / width
    col_dark = [count / height for count in col_dark_n]
    col_paper = [count / height for count in col_paper_n]
    return row_dark, row_paper, col_dark, col_paper


def _is_paper_band(dark: float, paper: float) -> bool:
    return paper >= CROP_PAPER_FRACTION and dark < CROP_DARK_FRACTION


def _is_printed_edge(dark: float, paper: float) -> bool:
    return dark >= CROP_DARK_FRACTION or paper <= 0.28


def _first_content_index(dark: list[float], paper: list[float], *, max_index: int) -> int:
    """Return the first printed-frame index, skipping cream paper and scanner dirt."""

    length = len(dark)
    if length == 0:
        return 0
    limit = min(max_index, length)
    index = 0
    while index < min(2, length - 1) and not _is_paper_band(dark[index], paper[index]):
        if any(
            _is_paper_band(dark[look], paper[look])
            for look in range(index + 1, min(index + 3, length))
        ):
            index += 1
            continue
        break
    if index >= length or not _is_paper_band(dark[index], paper[index]):
        return 0

    content_at = None
    cursor = index
    while cursor < limit:
        if _is_paper_band(dark[cursor], paper[cursor]):
            cursor += 1
            continue
        if _is_printed_edge(dark[cursor], paper[cursor]):
            resume = None
            for look in range(cursor + 1, min(cursor + 5, limit)):
                if dark[look] >= 0.30:
                    break
                if _is_paper_band(dark[look], paper[look]):
                    resume = look
                    break
            if resume is not None:
                cursor = resume
                continue
            content_at = cursor
            break
        cursor += 1

    if content_at is None:
        return 0
    return min(limit, content_at)


def crop_scan_margins(image: Image.Image) -> Image.Image:
    """Trim cream paper and scanner white down to a thin printed edge."""

    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 32 or height < 32:
        return rgb

    row_dark, row_paper, col_dark, col_paper = _edge_profiles(rgb)
    max_x = max(1, round(width * CROP_MAX_SIDE_FRACTION))
    max_y = max(1, round(height * CROP_MAX_SIDE_FRACTION))
    left = _first_content_index(col_dark, col_paper, max_index=max_x)
    top = _first_content_index(row_dark, row_paper, max_index=max_y)
    right = width - _first_content_index(
        list(reversed(col_dark)),
        list(reversed(col_paper)),
        max_index=max_x,
    )
    bottom = height - _first_content_index(
        list(reversed(row_dark)),
        list(reversed(row_paper)),
        max_index=max_y,
    )
    if right - left < width * CROP_MIN_KEEP_FRACTION:
        left, right = 0, width
    if bottom - top < height * CROP_MIN_KEEP_FRACTION:
        top, bottom = 0, height
    if left == 0 and top == 0 and right == width and bottom == height:
        return rgb
    return rgb.crop((left, top, right, bottom))


def encode_card_webp(payload: bytes, *, max_width: int = CARD_MAX_WIDTH) -> bytes:
    image = crop_scan_margins(Image.open(io.BytesIO(payload)).convert("RGB"))
    width, height = image.size
    if width > max_width:
        height = max(1, round(height * max_width / width))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=6)
    return buffer.getvalue()


def catalog() -> list[dict[str, str]]:
    entries = [
        {
            "id": "back",
            "file": "back.webp",
            "commons_file": BACK_COMMONS,
        }
    ]
    for number, commons_file in enumerate(MAJOR_COMMONS):
        entries.append(
            {
                "id": f"major:{number:02d}",
                "file": f"major-{number:02d}.webp",
                "commons_file": commons_file,
            }
        )
    for suit, prefix in SUIT_COMMONS_PREFIX.items():
        for number in range(1, 15):
            entries.append(
                {
                    "id": f"{suit}:{number:02d}",
                    "file": f"{suit}-{number:02d}.webp",
                    "commons_file": f"{prefix}{number:02d}.jpg",
                }
            )
    return entries


def expected_filenames() -> tuple[str, ...]:
    return tuple(entry["file"] for entry in catalog())


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _existing_source_map() -> dict[str, dict]:
    if not SOURCES_PATH.is_file():
        return {}
    payload = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in payload.get("cards", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-existing",
        action="store_true",
        help="Recrop and recompress bundled WebP files without downloading.",
    )
    args = parser.parse_args()

    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    previous = _existing_source_map()
    records = []
    for entry in catalog():
        destination = ASSET_DIR / entry["file"]
        if args.from_existing:
            if not destination.is_file():
                raise FileNotFoundError(destination)
            original = destination.read_bytes()
            source_url = str(previous.get(entry["id"], {}).get("source_url") or "")
            page_url = str(
                previous.get(entry["id"], {}).get("commons_page")
                or (
                    "https://commons.wikimedia.org/wiki/File:"
                    + quote(entry["commons_file"])
                )
            )
        else:
            request_url = COMMONS_FILEPATH + quote(entry["commons_file"])
            original, source_url = fetch(request_url)
            page_url = (
                "https://commons.wikimedia.org/wiki/File:"
                + quote(entry["commons_file"])
            )
        bundled = encode_card_webp(original)
        destination.write_bytes(bundled)
        records.append(
            {
                "id": entry["id"],
                "file": entry["file"],
                "commons_file": entry["commons_file"],
                "commons_page": page_url,
                "source_url": source_url,
                "sha256": sha256_bytes(bundled),
                "bytes": len(bundled),
            }
        )
        print(f"wrote {destination.relative_to(ROOT)} ({len(bundled)} bytes)")

    write_json(
        SOURCES_PATH,
        {
            "title": "Rider-Waite-Smith tarot (1909)",
            "artist": "Pamela Colman Smith",
            "designer": "A. E. Waite",
            "license": "Public domain",
            "notes": (
                "Published 1909. Public domain in the United States as a work "
                "published before 1930. Pamela Colman Smith died in 1951. "
                "Bundled WebP files are cropped to the printed card frame."
            ),
            "source": "Wikimedia Commons",
            "cards": records,
        },
    )
    print(f"wrote {SOURCES_PATH.relative_to(ROOT)} ({len(records)} files)")


if __name__ == "__main__":
    main()
