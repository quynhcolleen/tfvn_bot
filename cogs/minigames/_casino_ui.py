"""PNG table art for the interactive casino minigames."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Iterable, Sequence

import discord
from PIL import Image, ImageDraw, ImageFont

from cogs.minigames._playing_cards import Card, RANK_LABELS


TABLE_WIDTH = 960
TABLE_HEIGHT = 540
TABLE_SIZE = (TABLE_WIDTH, TABLE_HEIGHT)

FELT = (16, 84, 52)
FELT_DARK = (8, 48, 32)
GOLD = (214, 176, 72)
GOLD_SOFT = (168, 132, 48)
CREAM = (248, 244, 232)
INK = (24, 22, 28)
RED = (196, 36, 48)
NAVY = (28, 42, 86)
WHITE = (255, 255, 255)
MUTED = (214, 228, 214)
BANNER_WIN = (36, 122, 72)
BANNER_LOSS = (140, 36, 44)
BANNER_PUSH = (48, 78, 128)
BANNER_IDLE = (28, 64, 48)

_ROOT = Path(__file__).resolve().parents[2]
_FONT_PATH = _ROOT / "fonts" / "NotoSans-Variable.ttf"

SLOT_COLORS = {
    "cherry": (188, 28, 48),
    "bell": (236, 196, 64),
    "lemon": (236, 212, 72),
    "orange": (232, 132, 36),
    "seven": (220, 40, 48),
    "diamond": (72, 196, 220),
    "bar": (48, 48, 56),
}


def png_bytes(image: Image.Image) -> bytes:
    """Encode an RGB table as PNG bytes."""

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def attach_table_image(
    embed: discord.Embed,
    png: bytes | None,
    filename: str,
    *,
    for_edit: bool,
) -> dict:
    """Attach a table PNG to a Discord embed payload.

    ``embed`` is mutated with ``set_image`` when bytes are available. The
    returned dict is meant to be unpacked into ``send`` or ``edit``.
    """

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


@lru_cache(maxsize=24)
def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    if _FONT_PATH.is_file():
        font = ImageFont.truetype(str(_FONT_PATH), size=size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except OSError:
                pass
        return font
    return ImageFont.load_default(size=size)


def _center(count: int, item_width: int, gap: int, area_width: int, left: int) -> int:
    total = count * item_width + max(0, count - 1) * gap
    return left + max(0, (area_width - total) // 2)


def _card_size(count: int, max_width: int) -> tuple[int, int]:
    gap = 10
    width = min(84, (max_width - gap * max(0, count - 1)) // max(count, 1))
    width = max(52, width)
    height = round(width * 1.38)
    return width, height


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


def _new_table() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", TABLE_SIZE, FELT)
    overlay = Image.new("RGB", TABLE_SIZE, FELT_DARK)
    mask = Image.new("L", TABLE_SIZE, 0)
    ImageDraw.Draw(mask).ellipse((70, 48, TABLE_WIDTH - 70, TABLE_HEIGHT - 48), fill=170)
    image.paste(overlay, mask=mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (10, 10, TABLE_WIDTH - 11, TABLE_HEIGHT - 11),
        radius=30,
        outline=GOLD,
        width=4,
    )
    draw.rounded_rectangle(
        (22, 22, TABLE_WIDTH - 23, TABLE_HEIGHT - 23),
        radius=24,
        outline=GOLD_SOFT,
        width=1,
    )
    return image, draw


def _draw_banner(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    fill: tuple[int, int, int],
    top: int = 228,
) -> None:
    draw.rounded_rectangle(
        (80, top, TABLE_WIDTH - 80, top + 64),
        radius=18,
        fill=fill,
    )
    _text(
        draw,
        (TABLE_WIDTH / 2, top + 32),
        text,
        _font(24, bold=True),
        WHITE,
        anchor="mm",
    )


def _draw_header(
    draw: ImageDraw.ImageDraw,
    title: str,
    right: str,
) -> None:
    _text(draw, (48, 46), title, _font(32, bold=True), GOLD, anchor="lt")
    _text(draw, (TABLE_WIDTH - 48, 50), right, _font(20, bold=True), CREAM, anchor="rt")


def _draw_footer(draw: ImageDraw.ImageDraw, text: str) -> None:
    _text(
        draw,
        (TABLE_WIDTH / 2, TABLE_HEIGHT - 42),
        text,
        _font(18),
        MUTED,
        anchor="mm",
    )


def _suit_color(suit: str) -> tuple[int, int, int]:
    return RED if suit in {"♥", "♦"} else INK


def _ellipse(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    color: tuple[int, int, int],
) -> None:
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)


def _draw_suit(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    suit: str,
    color: tuple[int, int, int],
) -> None:
    left, top, right, bottom = box
    width = max(1, right - left)
    height = max(1, bottom - top)
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    if suit == "♦":
        draw.polygon(
            (
                (cx, top + height * 0.06),
                (right - width * 0.08, cy),
                (cx, bottom - height * 0.06),
                (left + width * 0.08, cy),
            ),
            fill=color,
        )
        return
    if suit == "♥":
        rx, ry = width * 0.28, height * 0.24
        _ellipse(draw, cx - rx * 0.72, cy - height * 0.12, rx, ry, color)
        _ellipse(draw, cx + rx * 0.72, cy - height * 0.12, rx, ry, color)
        draw.polygon(
            (
                (cx - width * 0.46, cy),
                (cx + width * 0.46, cy),
                (cx, bottom - height * 0.04),
            ),
            fill=color,
        )
        return
    if suit == "♠":
        rx, ry = width * 0.26, height * 0.22
        _ellipse(draw, cx - rx * 0.78, cy + height * 0.04, rx, ry, color)
        _ellipse(draw, cx + rx * 0.78, cy + height * 0.04, rx, ry, color)
        draw.polygon(
            (
                (cx - width * 0.46, cy + height * 0.08),
                (cx + width * 0.46, cy + height * 0.08),
                (cx, top + height * 0.02),
            ),
            fill=color,
        )
        stem = max(3, int(width * 0.12))
        draw.polygon(
            (
                (cx - stem, cy + height * 0.18),
                (cx + stem, cy + height * 0.18),
                (cx + width * 0.22, bottom - height * 0.02),
                (cx - width * 0.22, bottom - height * 0.02),
            ),
            fill=color,
        )
        return
    radius = min(width, height) * 0.22
    _ellipse(draw, cx, cy - height * 0.18, radius, radius, color)
    _ellipse(draw, cx - width * 0.22, cy + height * 0.04, radius, radius, color)
    _ellipse(draw, cx + width * 0.22, cy + height * 0.04, radius, radius, color)
    stem = max(3, int(width * 0.12))
    draw.polygon(
        (
            (cx - stem, cy + height * 0.08),
            (cx + stem, cy + height * 0.08),
            (cx + width * 0.2, bottom - height * 0.02),
            (cx - width * 0.2, bottom - height * 0.02),
        ),
        fill=color,
    )


def _draw_card_face(
    image: Image.Image,
    origin: tuple[int, int],
    card: Card,
    *,
    width: int,
    height: int,
    selected: bool = False,
) -> None:
    draw = ImageDraw.Draw(image)
    left, top = origin
    right, bottom = left + width - 1, top + height - 1
    outline = GOLD if selected else (168, 160, 148)
    radius = max(8, width // 10)
    if selected:
        draw.rounded_rectangle(
            (left - 3, top - 3, right + 3, bottom + 3),
            radius=radius + 2,
            outline=GOLD,
            width=3,
        )
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        fill=CREAM,
        outline=outline,
        width=3 if selected else 2,
    )
    color = _suit_color(card.suit)
    rank = RANK_LABELS.get(card.rank, str(card.rank))
    rank_font = _font(max(16, width // 3), bold=True)
    _text(draw, (left + 8, top + 6), rank, rank_font, color, anchor="lt")
    pip_box = (
        left + width // 4,
        top + height // 3,
        left + (width * 3) // 4,
        top + (height * 4) // 5,
    )
    _draw_suit(draw, pip_box, card.suit, color)
    _text(
        draw,
        (right - 8, bottom - 8),
        rank,
        rank_font,
        color,
        anchor="rb",
    )


def _draw_card_back(
    image: Image.Image,
    origin: tuple[int, int],
    *,
    width: int,
    height: int,
) -> None:
    radius = max(8, width // 10)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        fill=(*NAVY, 255),
        outline=(*GOLD, 255),
        width=2,
    )
    pattern = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pattern_draw = ImageDraw.Draw(pattern)
    for index in range(-height, width + height, 10):
        pattern_draw.line(
            (index, 0, index + height, height),
            fill=(64, 88, 150, 255),
            width=2,
        )
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (6, 6, width - 7, height - 7),
        radius=max(4, radius - 3),
        fill=255,
    )
    layer.paste(pattern, mask=mask)
    draw.rounded_rectangle(
        (6, 6, width - 7, height - 7),
        radius=max(4, radius - 3),
        outline=(*GOLD_SOFT, 255),
        width=1,
    )
    image.paste(layer, origin, layer)


def _draw_hand(
    image: Image.Image,
    cards: Sequence[Card | None],
    *,
    top: int,
    hidden: Iterable[bool] | None = None,
    selected: Iterable[int] = (),
    max_width: int = 760,
) -> None:
    count = max(1, len(cards))
    width, height = _card_size(count, max_width)
    gap = 10
    left = _center(count, width, gap, max_width, (TABLE_WIDTH - max_width) // 2)
    hidden_flags = list(hidden) if hidden is not None else [False] * count
    selected_set = set(selected)
    for index, card in enumerate(cards):
        origin = (left + index * (width + gap), top)
        face_down = index >= len(hidden_flags) or hidden_flags[index] or card is None
        if face_down:
            _draw_card_back(image, origin, width=width, height=height)
            continue
        _draw_card_face(
            image,
            origin,
            card,
            width=width,
            height=height,
            selected=index in selected_set,
        )


def _draw_label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    title: str,
    detail: str,
) -> None:
    _text(draw, xy, title, _font(18, bold=True), GOLD, anchor="lt")
    if detail:
        _text(
            draw,
            (xy[0] + draw.textlength(title, font=_font(18, bold=True)) + 16, xy[1] + 2),
            detail,
            _font(18, bold=True),
            CREAM,
            anchor="lt",
        )


def render_blackjack_table(
    *,
    player_hand: Sequence[Card],
    dealer_hand: Sequence[Card],
    player_total: int,
    dealer_total: int | None,
    reveal_dealer: bool,
    bet: int,
    balance: int,
    status: str,
    banner_fill: tuple[int, int, int] = BANNER_IDLE,
) -> bytes:
    """Render a Blackjack felt with the live hands."""

    image, draw = _new_table()
    _draw_header(draw, "BLACKJACK", f"Cược {bet:,} TC")
    dealer_hidden = [False] + [not reveal_dealer] * max(0, len(dealer_hand) - 1)
    dealer_score = str(dealer_total) if reveal_dealer and dealer_total is not None else "?"
    _draw_label(draw, (48, 82), "NHÀ CÁI", f"Điểm {dealer_score}")
    _draw_hand(image, dealer_hand, top=108, hidden=dealer_hidden)
    _draw_banner(draw, status, fill=banner_fill)
    _draw_label(draw, (48, 318), "BÀI CỦA BẠN", f"Điểm {player_total}")
    _draw_hand(image, player_hand, top=348)
    _draw_footer(draw, f"Số dư {balance:,} TC")
    return png_bytes(image)


def render_poker_table(
    *,
    player_hand: Sequence[Card],
    dealer_hand: Sequence[Card],
    reveal_dealer: bool,
    selected: Sequence[int] = (),
    bet: int,
    balance: int,
    status: str,
    player_rank: str = "",
    dealer_rank: str = "",
    banner_fill: tuple[int, int, int] = BANNER_IDLE,
) -> bytes:
    """Render a five-card-draw table, hiding the dealer until showdown."""

    image, draw = _new_table()
    _draw_header(draw, "POKER 5 LÁ", f"Cược {bet:,} TC")
    dealer_detail = dealer_rank if reveal_dealer and dealer_rank else "Úp bài"
    _draw_label(draw, (48, 82), "NHÀ CÁI", dealer_detail)
    _draw_hand(
        image,
        dealer_hand,
        top=108,
        hidden=[not reveal_dealer] * len(dealer_hand),
    )
    _draw_banner(draw, status, fill=banner_fill)
    _draw_label(draw, (48, 318), "BÀI CỦA BẠN", player_rank)
    _draw_hand(image, player_hand, top=348, selected=selected)
    _draw_footer(draw, f"Số dư {balance:,} TC")
    return png_bytes(image)


def _draw_slot_symbol(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    symbol: str,
    *,
    dim: bool = False,
) -> None:
    left, top, right, bottom = box
    color = SLOT_COLORS.get(symbol, CREAM)
    if dim:
        color = tuple(max(0, value // 2) for value in color)
    cx = (left + right) / 2
    cy = (top + bottom) / 2
    if symbol == "cherry":
        draw.ellipse((cx - 28, cy - 8, cx - 4, cy + 16), fill=color)
        draw.ellipse((cx + 2, cy - 8, cx + 26, cy + 16), fill=color)
        draw.line((cx - 8, cy - 8, cx, cy - 28), fill=(48, 120, 56), width=4)
        draw.line((cx + 10, cy - 8, cx, cy - 28), fill=(48, 120, 56), width=4)
        return
    if symbol == "bell":
        draw.ellipse((cx - 34, top + 28, cx + 34, cy + 10), fill=color)
        draw.polygon(
            (
                (cx - 36, cy - 6),
                (cx + 36, cy - 6),
                (cx + 30, cy + 22),
                (cx - 30, cy + 22),
            ),
            fill=color,
        )
        draw.rectangle((cx - 32, cy + 18, cx + 32, cy + 28), fill=color)
        draw.ellipse((cx - 10, cy + 22, cx + 10, cy + 38), fill=GOLD)
        return
    if symbol == "lemon":
        draw.ellipse((left + 14, top + 36, right - 14, bottom - 36), fill=color)
        return
    if symbol == "orange":
        draw.ellipse((left + 22, top + 28, right - 22, bottom - 28), fill=color)
        return
    if symbol == "diamond":
        draw.polygon(
            ((cx, top + 16), (right - 18, cy), (cx, bottom - 16), (left + 18, cy)),
            fill=color,
        )
        return
    if symbol == "bar":
        draw.rounded_rectangle(
            (left + 16, cy - 18, right - 16, cy + 18),
            radius=8,
            fill=color,
        )
        _text(draw, (cx, cy), "BAR", _font(22, bold=True), WHITE, anchor="mm")
        return
    _text(draw, (cx, cy), "7", _font(64, bold=True), color, anchor="mm")


def render_slot_table(
    *,
    reels: Sequence[str],
    bet: int,
    balance: int,
    status: str,
    spinning: bool = False,
    banner_fill: tuple[int, int, int] = BANNER_IDLE,
) -> bytes:
    """Render a three-reel slot cabinet."""

    image, draw = _new_table()
    _draw_header(draw, "MÁY SLOT", f"Cược {bet:,} TC")
    reel_width, reel_height = 170, 196
    gap = 28
    left = _center(3, reel_width, gap, 640, 160)
    top = 86
    for index, symbol in enumerate(reels):
        x = left + index * (reel_width + gap)
        draw.rounded_rectangle(
            (x, top, x + reel_width, top + reel_height),
            radius=22,
            fill=(12, 18, 28),
            outline=GOLD,
            width=3,
        )
        _draw_slot_symbol(
            draw,
            (x, top, x + reel_width, top + reel_height),
            symbol,
            dim=spinning,
        )
    _draw_banner(draw, status, fill=banner_fill, top=318)
    _draw_footer(
        draw,
        f"Ba giống = 100 TC · Hai giống = 10 TC · Số dư {balance:,} TC",
    )
    return png_bytes(image)


def _draw_die(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    value: int,
    *,
    size: int = 120,
) -> None:
    left, top = origin
    right, bottom = left + size, top + size
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=18,
        fill=CREAM,
        outline=INK,
        width=3,
    )
    pip = max(8, size // 10)
    positions = {
        1: ((0.5, 0.5),),
        2: ((0.28, 0.28), (0.72, 0.72)),
        3: ((0.28, 0.28), (0.5, 0.5), (0.72, 0.72)),
        4: ((0.28, 0.28), (0.72, 0.28), (0.28, 0.72), (0.72, 0.72)),
        5: (
            (0.28, 0.28),
            (0.72, 0.28),
            (0.5, 0.5),
            (0.28, 0.72),
            (0.72, 0.72),
        ),
        6: (
            (0.28, 0.28),
            (0.72, 0.28),
            (0.28, 0.5),
            (0.72, 0.5),
            (0.28, 0.72),
            (0.72, 0.72),
        ),
    }
    for px, py in positions[value]:
        cx = left + px * size
        cy = top + py * size
        draw.ellipse((cx - pip, cy - pip, cx + pip, cy + pip), fill=INK)


def render_sicbo_table(
    *,
    dice: Sequence[int] | None,
    bet: int,
    balance: int,
    choice: str | None,
    status: str,
    banner_fill: tuple[int, int, int] = BANNER_IDLE,
) -> bytes:
    """Render a Tài/Xỉu board with optional revealed dice."""

    image, draw = _new_table()
    _draw_header(draw, "SICBO · TÀI XỈU", f"Cược {bet:,} TC")
    die_size = 112
    gap = 24
    left = _center(3, die_size, gap, 480, 240)
    top = 92
    shown = tuple(dice) if dice is not None else (0, 0, 0)
    for index, value in enumerate(shown):
        x = left + index * (die_size + gap)
        if value:
            _draw_die(draw, (x, top), value, size=die_size)
        else:
            draw.rounded_rectangle(
                (x, top, x + die_size, top + die_size),
                radius=18,
                fill=NAVY,
                outline=GOLD,
                width=3,
            )
            _text(
                draw,
                (x + die_size / 2, top + die_size / 2),
                "?",
                _font(40, bold=True),
                GOLD,
                anchor="mm",
            )

    options = (
        ("TÀI", "11–17 · 1:1", "big"),
        ("XỈU", "4–10 · 1:1", "small"),
        ("BỘ BA", "Ba mặt giống · 30:1", "triple"),
    )
    box_width, box_height = 250, 78
    box_gap = 18
    box_left = _center(3, box_width, box_gap, 804, 78)
    box_top = 330
    for index, (label, detail, key) in enumerate(options):
        x = box_left + index * (box_width + box_gap)
        selected = choice == key
        draw.rounded_rectangle(
            (x, box_top, x + box_width, box_top + box_height),
            radius=16,
            fill=(36, 96, 64) if selected else (12, 40, 28),
            outline=GOLD if selected else GOLD_SOFT,
            width=3 if selected else 1,
        )
        _text(
            draw,
            (x + box_width / 2, box_top + 28),
            label,
            _font(22, bold=True),
            GOLD if selected else CREAM,
            anchor="mm",
        )
        _text(
            draw,
            (x + box_width / 2, box_top + 54),
            detail,
            _font(14),
            MUTED,
            anchor="mm",
        )
    _draw_banner(draw, status, fill=banner_fill)
    _draw_footer(draw, f"Số dư {balance:,} TC")
    return png_bytes(image)
