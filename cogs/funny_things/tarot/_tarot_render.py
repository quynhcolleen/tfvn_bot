"""PNG spread art using bundled Rider-Waite-Smith card scans."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import discord
from PIL import Image, ImageChops, ImageDraw, ImageFont

from cogs.funny_things.tarot._tarot_helpers import DrawnCard, TarotCard, TarotReading


CLOTH = (28, 16, 48)
CLOTH_DARK = (16, 8, 32)
GOLD = (214, 176, 72)
GOLD_SOFT = (168, 132, 48)
INK = (24, 22, 28)
NAVY = (32, 28, 78)
MUTED = (214, 196, 230)
REVERSED_RIBBON = (140, 36, 56)
BEZEL_ROSE = (230, 148, 176)
BEZEL_PASTEL = (255, 192, 214)
BEZEL_PEARL = (255, 230, 238)
BEZEL_LIP = (206, 112, 148)

CARD_SIZES = {
    "single": (168, 280),
    "three": (138, 230),
    "five": (110, 184),
    "seven": (92, 154),
    "celtic": (84, 142),
}

TAROT_FILENAME = "tarot.png"
BACK_FILENAME = "back.webp"

_ROOT = Path(__file__).resolve().parents[3]
_FONT_PATH = _ROOT / "fonts" / "NotoSans-Variable.ttf"
TAROT_ASSET_DIR = _ROOT / "assets" / "tarot"


@dataclass(frozen=True)
class SlotLayout:
    cx: int
    cy: int
    rotation: int = 0
    caption_dx: int = 0
    caption_dy: int = 0
    badge_dx: int = 0
    badge_dy: int = 0


@dataclass(frozen=True)
class SpreadArt:
    width: int
    height: int
    slots: tuple[SlotLayout, ...]


def png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def attach_spread_image(
    embed: discord.Embed,
    png: bytes | None,
    filename: str,
    *,
    for_edit: bool,
) -> dict:
    extra: dict = {}
    if png:
        embed.set_image(url=f"attachment://{filename}")
        image = discord.File(BytesIO(png), filename=filename)
        if for_edit:
            extra["attachments"] = [image]
        else:
            extra["file"] = image
    elif for_edit:
        extra["attachments"] = []
    return extra


def card_art_path(card: TarotCard) -> Path:
    return TAROT_ASSET_DIR / card.asset_filename


def back_art_path() -> Path:
    return TAROT_ASSET_DIR / BACK_FILENAME


@lru_cache(maxsize=24)
def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    if _FONT_PATH.is_file():
        font = ImageFont.truetype(str(_FONT_PATH), size=size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except OSError:
                pass
        return font
    return ImageFont.load_default(size=size)


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    *,
    anchor: str = "lt",
) -> None:
    draw.text(xy, value, font=font, fill=fill, anchor=anchor)


def spread_art(spread_key: str) -> SpreadArt:
    if spread_key == "single":
        return SpreadArt(640, 520, (SlotLayout(320, 268),))
    if spread_key == "three":
        return SpreadArt(
            960,
            540,
            (
                SlotLayout(210, 286),
                SlotLayout(480, 286),
                SlotLayout(750, 286),
            ),
        )
    if spread_key == "five":
        return SpreadArt(
            920,
            860,
            (
                SlotLayout(460, 400),
                SlotLayout(460, 650),
                SlotLayout(230, 400),
                SlotLayout(460, 150),
                SlotLayout(690, 400),
            ),
        )
    if spread_key == "seven":
        return SpreadArt(
            1120,
            620,
            (
                SlotLayout(120, 430),
                SlotLayout(270, 300),
                SlotLayout(430, 210),
                SlotLayout(560, 180),
                SlotLayout(690, 210),
                SlotLayout(850, 300),
                SlotLayout(1000, 430),
            ),
        )
    if spread_key == "celtic":
        return SpreadArt(
            1140,
            1040,
            (
                SlotLayout(400, 500),
                SlotLayout(
                    400,
                    500,
                    rotation=90,
                    caption_dx=132,
                    caption_dy=-60,
                    badge_dx=80,
                    badge_dy=10,
                ),
                SlotLayout(400, 740),
                SlotLayout(190, 500),
                SlotLayout(400, 260),
                SlotLayout(630, 500),
                SlotLayout(960, 890),
                SlotLayout(960, 670),
                SlotLayout(960, 450),
                SlotLayout(960, 230),
            ),
        )
    raise KeyError(f"No tarot art layout for spread {spread_key}")


def _new_cloth(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), CLOTH)
    overlay = Image.new("RGB", (width, height), CLOTH_DARK)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        (50, 36, width - 50, height - 36),
        fill=170,
    )
    image.paste(overlay, mask=mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (10, 10, width - 11, height - 11),
        radius=28,
        outline=GOLD,
        width=4,
    )
    draw.rounded_rectangle(
        (22, 22, width - 23, height - 23),
        radius=22,
        outline=GOLD_SOFT,
        width=1,
    )
    return image, draw


def _contain(image: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = image.size
    scale = min(width / max(src_w, 1), height / max(src_h, 1))
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2))
    return canvas


def _card_radius(width: int) -> int:
    return max(8, width // 12)


def _bezel_thickness(width: int) -> int:
    return max(4, round(width / 24))


def _rounded_layer(image: Image.Image, radius: int) -> Image.Image:
    width, height = image.size
    fitted = image.convert("RGBA")
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        fill=255,
    )
    red, green, blue, alpha = fitted.split()
    return Image.merge("RGBA", (red, green, blue, ImageChops.multiply(alpha, mask)))


def _paint_pastel_bezel(width: int, height: int) -> Image.Image:
    """Enamel-style pastel pink rim: rose, cotton pink, pearl, raspberry lip."""

    radius = _card_radius(width)
    thickness = _bezel_thickness(width)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        fill=(*BEZEL_ROSE, 255),
    )
    draw.rounded_rectangle(
        (1, 1, width - 2, height - 2),
        radius=max(7, radius - 1),
        fill=(*BEZEL_PASTEL, 255),
    )
    if thickness >= 4:
        pearl = max(2, thickness - 3)
        draw.rounded_rectangle(
            (pearl, pearl, width - 1 - pearl, height - 1 - pearl),
            radius=max(6, radius - pearl),
            fill=(*BEZEL_PEARL, 255),
        )
    lip = max(2, thickness - 1)
    draw.rounded_rectangle(
        (lip, lip, width - 1 - lip, height - 1 - lip),
        radius=max(5, radius - lip),
        fill=(*BEZEL_LIP, 255),
    )
    return layer


def _frame_card(inner: Image.Image, width: int, height: int) -> Image.Image:
    thickness = _bezel_thickness(width)
    radius = _card_radius(width)
    layer = _paint_pastel_bezel(width, height)
    inner_w = max(1, width - 2 * thickness)
    inner_h = max(1, height - 2 * thickness)
    window = _rounded_layer(
        _contain(inner.convert("RGBA"), inner_w, inner_h),
        max(4, radius - thickness),
    )
    layer.paste(window, (thickness, thickness), window)
    return layer


def _round_card(image: Image.Image, width: int, height: int) -> Image.Image:
    return _frame_card(image, width, height)


def _placeholder_back(width: int, height: int) -> Image.Image:
    return _frame_card(
        Image.new("RGBA", (width, height), (*NAVY, 255)),
        width,
        height,
    )


@lru_cache(maxsize=96)
def _load_art(path: str) -> Image.Image | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    with Image.open(file_path) as image:
        return image.convert("RGBA").copy()


def _card_face(drawn: DrawnCard, width: int, height: int) -> Image.Image:
    art = _load_art(str(card_art_path(drawn.card)))
    if art is None:
        layer = _placeholder_back(width, height)
    else:
        layer = _round_card(art, width, height)
    if drawn.reversed:
        layer = layer.rotate(180, expand=False, resample=Image.Resampling.BICUBIC)
    return layer


def _card_back(width: int, height: int) -> Image.Image:
    art = _load_art(str(back_art_path()))
    if art is None:
        return _placeholder_back(width, height)
    return _round_card(art, width, height)


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    index: int,
) -> None:
    radius = 12
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=GOLD,
        outline=INK,
        width=1,
    )
    _text(draw, (cx, cy), str(index), _font(14, bold=True), INK, anchor="mm")


def render_tarot_spread(reading: TarotReading) -> bytes:
    """Paint face-down and face-up cards for the current reveal state."""

    art = spread_art(reading.spread.key)
    if len(art.slots) != reading.spread.card_count:
        raise ValueError("Spread art slots must match the spread size")
    width, height = CARD_SIZES[reading.spread.key]
    image, draw = _new_cloth(art.width, art.height)
    _text(
        draw,
        (36, 40),
        f"TAROT • {reading.spread.name_vi.upper()}",
        _font(26, bold=True),
        GOLD,
        anchor="lt",
    )
    subtitle = reading.question or "Không ghi câu hỏi"
    _text(
        draw,
        (art.width - 36, 44),
        subtitle[:42] + ("…" if len(subtitle) > 42 else ""),
        _font(16),
        MUTED,
        anchor="rt",
    )

    for drawn, revealed, slot in zip(
        reading.cards,
        reading.revealed,
        art.slots,
        strict=True,
    ):
        card_image = (
            _card_face(drawn, width, height) if revealed else _card_back(width, height)
        )
        if slot.rotation:
            card_image = card_image.rotate(
                slot.rotation,
                expand=True,
                resample=Image.Resampling.BICUBIC,
            )
        visual_w, visual_h = card_image.size
        origin = (slot.cx - visual_w // 2, slot.cy - visual_h // 2)
        image.paste(card_image, origin, card_image)
        badge_x = origin[0] - 4 + slot.badge_dx
        badge_y = origin[1] - 4 + slot.badge_dy
        _draw_badge(draw, badge_x, badge_y, drawn.position.index)
        caption_fill = REVERSED_RIBBON if revealed and drawn.reversed else GOLD
        _text(
            draw,
            (
                slot.cx + slot.caption_dx,
                slot.cy + visual_h // 2 + 14 + slot.caption_dy,
            ),
            drawn.position.name_vi,
            _font(14, bold=True),
            caption_fill,
            anchor="mt",
        )

    footer = (
        "Đã lật hết bài"
        if reading.all_revealed
        else f"Úp {reading.spread.card_count - reading.revealed_count} lá"
    )
    _text(
        draw,
        (art.width / 2, art.height - 32),
        footer,
        _font(16),
        MUTED,
        anchor="mm",
    )
    return png_bytes(image)
