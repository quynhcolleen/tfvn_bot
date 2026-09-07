"""Discord dark-theme chat mockup rendering for highlight posts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import unicodedata

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from cogs.utils._quote_card import (
    _FallbackFont,
    _HORIZONTAL_WHITESPACE,
    _UNSUPPORTED_GLYPH,
    _load_fallback_font,
    _load_font,
    _mix,
    _safe_accent,
    _single_line,
    _text_block_height,
    _truncate_to_width,
    normalize_quote_text,
    wrap_quote_text,
)


CARD_WIDTH = 960
MAX_CARD_HEIGHT = 2_000
MIN_CARD_HEIGHT = 176
AVATAR_SIZE = 80
HEADER_HEIGHT = 64
PADDING = 32
AVATAR_TEXT_GAP = 16
NAME_TIMESTAMP_GAP = 12
TEXT_TOP_GAP = 6
IMAGE_TOP_GAP = 16
IMAGE_MAX_HEIGHT = 720
IMAGE_CORNER_RADIUS = 12
BODY_FONT_SIZE = 32
NAME_FONT_SIZE = 32
TIMESTAMP_FONT_SIZE = 24
HEADER_FONT_SIZE = 32
LINE_SPACING = 10
MIN_IMAGE_HEIGHT = 80

BACKGROUND = (49, 51, 56)
HEADER_BACKGROUND = (43, 45, 49)
BODY_TEXT = (219, 222, 225)
MUTED_TEXT = (148, 155, 164)
HEADER_TEXT = (126, 132, 151)
DEFAULT_NAME = (242, 243, 245)
HEADER_DIVIDER = (30, 31, 34)
DEFAULT_ACCENT = (88, 101, 242)

TEXT_MAX_WIDTH = CARD_WIDTH - PADDING - AVATAR_SIZE - AVATAR_TEXT_GAP - PADDING
VIETNAM_TIMEZONE = timezone(timedelta(hours=7), name="UTC+07:00")


def _channel_name_aliases() -> dict[int, str | None]:
    """Map Discord-name decorations the bundled fonts cannot draw."""
    table: dict[int, str | None] = {
        ord(_UNSUPPORTED_GLYPH): None,
        0x00A6: "|",  # broken bar
        0x30FB: "·",  # katakana middle dot
        0xFF65: "·",  # halfwidth katakana middle dot
        0x301C: "~",  # wave dash
        0x3030: "~",  # wavy dash
        0x3008: "<",
        0x3009: ">",
        0x300A: "<",
        0x300B: ">",
        0x300C: "[",  # 「
        0x300D: "]",
        0x300E: "[",
        0x300F: "]",
        0x3010: "[",  # 【
        0x3011: "]",
        0x3014: "[",
        0x3015: "]",
        0x3016: "[",
        0x3017: "]",
        0x3018: "[",
        0x3019: "]",
        0x301A: "[",
        0x301B: "]",
        0xFE31: "|",
        0xFE33: "|",
        0xFF62: "[",  # ｢
        0xFF63: "]",
    }
    for codepoint in range(0x2500, 0x2580):
        name = unicodedata.name(chr(codepoint), "")
        has_vertical = "VERTICAL" in name
        has_horizontal = "HORIZONTAL" in name
        if has_vertical and not has_horizontal:
            table[codepoint] = "|"
        elif has_horizontal and not has_vertical:
            table[codepoint] = "-"
        else:
            table[codepoint] = "+"
    return table


_CHANNEL_NAME_TRANSLATION = str.maketrans(_channel_name_aliases())


def format_highlight_channel_name(channel_name: str) -> str:
    """Build a channel header that bundled fonts can draw.

    Discord names often use box-drawing separators, CJK brackets, or flag
    emoji. Do not convert emoji to :shortcodes:, and replace decorations the
    bundled Noto set cannot render so the header does not show tofu boxes.
    The leading # used in Discord UI is omitted.
    """
    collapsed = _HORIZONTAL_WHITESPACE.sub(
        " ",
        unicodedata.normalize("NFKC", channel_name.replace("\n", " ")),
    ).strip()
    collapsed = collapsed.translate(_CHANNEL_NAME_TRANSLATION).strip()
    collapsed = _HORIZONTAL_WHITESPACE.sub(" ", collapsed)
    collapsed = collapsed.lstrip("#").strip()
    return collapsed or "channel"


def _drawable_channel_label(label: str, font: _FallbackFont) -> str:
    """Drop remaining glyphs none of the fallback fonts can draw."""
    drawn = "".join(
        run.replace(_UNSUPPORTED_GLYPH, "")
        for run, _ in font._font_runs(label)
    )
    drawn = _HORIZONTAL_WHITESPACE.sub(" ", drawn).strip()
    drawn = drawn.lstrip("#").strip()
    return drawn or "channel"


def normalize_highlight_text(
    content: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Prepare message text for a chat mockup; empty is optional for images."""
    try:
        return normalize_quote_text(content)
    except ValueError:
        if allow_empty:
            return ""
        raise ValueError(
            "Tin nhắn không có chữ hoặc ảnh để tạo highlight."
        ) from None


def format_highlight_timestamp(
    created_at: datetime,
    *,
    now: datetime | None = None,
) -> str:
    """Format a Discord-style timestamp in Vietnam time (UTC+7)."""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    local = created_at.astimezone(VIETNAM_TIMEZONE)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_local = current.astimezone(VIETNAM_TIMEZONE)
    clock = local.strftime("%H:%M")
    if local.date() == current_local.date():
        return f"Hôm nay lúc {clock}"
    return f"{local.strftime('%d/%m/%Y')} {clock}"


def _placeholder_avatar(
    display_name: str,
    accent: tuple[int, int, int],
) -> Image.Image:
    avatar = Image.new(
        "RGB",
        (AVATAR_SIZE, AVATAR_SIZE),
        _mix((30, 33, 46), accent, 0.55),
    )
    draw = ImageDraw.Draw(avatar)
    initial = next(
        (
            character.upper()
            for character in display_name
            if character.isalnum()
        ),
        "?",
    )
    font = _load_font(36, bold=True)
    box = draw.textbbox((0, 0), initial, font=font)
    x = (AVATAR_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (AVATAR_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), initial, font=font, fill=(248, 249, 255))
    return avatar


def _prepare_avatar(
    avatar_bytes: bytes | None,
    display_name: str,
    accent: tuple[int, int, int],
) -> Image.Image:
    if avatar_bytes:
        try:
            with Image.open(BytesIO(avatar_bytes)) as source:
                return ImageOps.fit(
                    source.convert("RGB"),
                    (AVATAR_SIZE, AVATAR_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, ValueError, UnidentifiedImageError):
            pass
    return _placeholder_avatar(display_name, accent)


def _prepare_attachment_image(
    image_bytes: bytes | None,
    max_width: int,
    max_height: int,
) -> Image.Image | None:
    if not image_bytes or max_width <= 0 or max_height <= 0:
        return None
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            frame = source.convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    return ImageOps.contain(
        frame,
        (max_width, max_height),
        method=Image.Resampling.LANCZOS,
    )


def _paste_circle(
    card: Image.Image,
    avatar: Image.Image,
    xy: tuple[int, int],
) -> None:
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255)
    card.paste(avatar, xy, mask)


def _paste_rounded(
    card: Image.Image,
    image: Image.Image,
    xy: tuple[int, int],
    radius: int,
) -> None:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.width, image.height),
        radius=radius,
        fill=255,
    )
    card.paste(image, xy, mask)


def _name_color(accent_rgb: tuple[int, int, int] | None) -> tuple[int, int, int]:
    if accent_rgb is None:
        return DEFAULT_NAME
    return _safe_accent(accent_rgb)


def _card_height(
    *,
    name_height: int,
    lines: list[str],
    font,
    spacing: int,
    image_height: int,
) -> int:
    body = name_height
    if lines:
        body += TEXT_TOP_GAP + _text_block_height(lines, font, spacing)
    if image_height:
        body += IMAGE_TOP_GAP + image_height
    body = max(body, AVATAR_SIZE)
    return max(MIN_CARD_HEIGHT, HEADER_HEIGHT + PADDING + body + PADDING)


def _fit_body(
    *,
    text: str,
    font,
    spacing: int,
    name_height: int,
    attachment: Image.Image | None,
) -> tuple[list[str], Image.Image | None]:
    lines = wrap_quote_text(text, font, TEXT_MAX_WIDTH) if text else []
    image = attachment
    truncated = False

    def height_for(current_lines: list[str], image_height: int) -> int:
        return _card_height(
            name_height=name_height,
            lines=current_lines,
            font=font,
            spacing=spacing,
            image_height=image_height,
        )

    image_height = image.height if image is not None else 0
    while (
        len(lines) > 1
        and height_for(lines, image_height) > MAX_CARD_HEIGHT
    ):
        lines.pop()
        truncated = True

    if height_for(lines, image_height) > MAX_CARD_HEIGHT and image is not None:
        overflow = height_for(lines, image_height) - MAX_CARD_HEIGHT
        new_height = image.height - overflow
        if new_height < MIN_IMAGE_HEIGHT:
            image = None
            image_height = 0
        else:
            new_width = max(
                1,
                round(image.width * (new_height / image.height)),
            )
            image = image.resize(
                (new_width, new_height),
                Image.Resampling.LANCZOS,
            )
            image_height = image.height

    if height_for(lines, image_height) > MAX_CARD_HEIGHT and lines:
        while (
            len(lines) > 1
            and height_for(lines, image_height) > MAX_CARD_HEIGHT
        ):
            lines.pop()
            truncated = True

    if truncated and lines:
        lines[-1] = _truncate_to_width(
            lines[-1],
            font,
            TEXT_MAX_WIDTH,
            force_suffix=True,
        )
    return lines, image


def render_highlight_card(
    *,
    avatar_bytes: bytes | None,
    display_name: str,
    channel_name: str,
    message_text: str,
    timestamp_label: str,
    accent_rgb: tuple[int, int, int] | None = None,
    attachment_bytes: bytes | None = None,
) -> bytes:
    """Render a variable-height Discord dark-theme chat PNG."""
    allow_empty = attachment_bytes is not None
    text = normalize_highlight_text(message_text, allow_empty=allow_empty)
    display_name = _single_line(display_name, "Discord user")
    timestamp_label = _single_line(timestamp_label, "Discord")
    name_color = _name_color(accent_rgb)
    accent = name_color if accent_rgb is not None else DEFAULT_ACCENT

    name_font = _load_fallback_font(NAME_FONT_SIZE, bold=True)
    timestamp_font = _load_fallback_font(TIMESTAMP_FONT_SIZE)
    header_font = _load_fallback_font(HEADER_FONT_SIZE)
    body_font = _load_fallback_font(BODY_FONT_SIZE)
    name_height = max(
        1,
        name_font.primary.getmetrics()[0] + name_font.primary.getmetrics()[1],
    )

    attachment = _prepare_attachment_image(
        attachment_bytes,
        TEXT_MAX_WIDTH,
        IMAGE_MAX_HEIGHT,
    )
    if not text and attachment is None:
        raise ValueError("Tin nhắn không có chữ hoặc ảnh để tạo highlight.")

    lines, attachment = _fit_body(
        text=text,
        font=body_font,
        spacing=LINE_SPACING,
        name_height=name_height,
        attachment=attachment,
    )
    image_height = attachment.height if attachment is not None else 0
    height = _card_height(
        name_height=name_height,
        lines=lines,
        font=body_font,
        spacing=LINE_SPACING,
        image_height=image_height,
    )
    if height > MAX_CARD_HEIGHT:
        height = MAX_CARD_HEIGHT

    card = Image.new("RGB", (CARD_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, CARD_WIDTH, HEADER_HEIGHT), fill=HEADER_BACKGROUND)
    draw.line(
        (0, HEADER_HEIGHT - 1, CARD_WIDTH, HEADER_HEIGHT - 1),
        fill=HEADER_DIVIDER,
    )

    channel_label = _drawable_channel_label(
        format_highlight_channel_name(channel_name),
        header_font,
    )
    safe_channel = _truncate_to_width(
        channel_label,
        header_font,
        CARD_WIDTH - (2 * PADDING),
    )
    header_box = header_font._text_bbox(safe_channel)
    header_text_height = header_box[3] - header_box[1]
    header_y = int((HEADER_HEIGHT - header_text_height) / 2 - header_box[1])
    header_font.draw_text(
        draw,
        (PADDING, max(0, header_y)),
        safe_channel,
        HEADER_TEXT,
    )

    avatar = _prepare_avatar(avatar_bytes, display_name, accent)
    avatar_x = PADDING
    avatar_y = HEADER_HEIGHT + PADDING
    _paste_circle(card, avatar, (avatar_x, avatar_y))

    text_x = PADDING + AVATAR_SIZE + AVATAR_TEXT_GAP
    name_y = HEADER_HEIGHT + PADDING
    timestamp_width = int(timestamp_font.getlength(timestamp_label))
    name_budget = TEXT_MAX_WIDTH - timestamp_width - NAME_TIMESTAMP_GAP
    if name_budget < 48:
        timestamp_label = _truncate_to_width(
            timestamp_label,
            timestamp_font,
            max(48, TEXT_MAX_WIDTH // 3),
        )
        timestamp_width = int(timestamp_font.getlength(timestamp_label))
        name_budget = TEXT_MAX_WIDTH - timestamp_width - NAME_TIMESTAMP_GAP
    safe_name = _truncate_to_width(
        display_name,
        name_font,
        max(48, name_budget),
    )
    name_font.draw_text(draw, (text_x, name_y), safe_name, name_color)
    timestamp_x = text_x + int(name_font.getlength(safe_name)) + NAME_TIMESTAMP_GAP
    timestamp_font.draw_text(
        draw,
        (timestamp_x, name_y + 6),
        timestamp_label,
        MUTED_TEXT,
    )

    content_y = name_y + name_height + TEXT_TOP_GAP
    if lines:
        body_font.draw_multiline(
            draw,
            (text_x, content_y),
            lines,
            BODY_TEXT,
            LINE_SPACING,
        )
        content_y += _text_block_height(lines, body_font, LINE_SPACING)

    if attachment is not None:
        image_y = content_y + IMAGE_TOP_GAP if lines else content_y
        _paste_rounded(
            card,
            attachment,
            (text_x, image_y),
            IMAGE_CORNER_RADIUS,
        )

    output = BytesIO()
    card.save(output, format="PNG", optimize=True)
    return output.getvalue()
