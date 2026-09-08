"""Discord dark-theme chat mockup rendering for highlight posts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import unicodedata

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from cogs.utils._highlight_font import load_highlight_font as _load_fallback_font
from cogs.utils._highlight_media import (
    MAX_HIGHLIGHT_EMBEDS,
    MAX_HIGHLIGHT_IMAGES,
    HighlightEmbed,
)
from cogs.utils._highlight_text import (
    EmbedTextFont,
    TextLine,
    truncate_embed_line,
    wrap_embed_text,
)
from cogs.utils._quote_card import (
    MAX_NORMALIZED_TEXT_LENGTH,
    MAX_SOURCE_TEXT_LENGTH,
    _CUSTOM_EMOJI,
    _FallbackFont,
    _HORIZONTAL_WHITESPACE,
    _UNSUPPORTED_GLYPH,
    _load_font,
    _mix,
    _safe_accent,
    _single_line,
    _text_block_height,
    _truncate_to_width,
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
MAX_IMAGE_PIXELS = 24_000_000
GALLERY_GAP = 12
EMBED_PADDING = 20
EMBED_GAP = 10
EMBED_FONT_SIZE = 24
EMBED_TITLE_FONT_SIZE = 28
EMBED_META_FONT_SIZE = 20
EMBED_THUMBNAIL_SIZE = 120
EMBED_BACKGROUND = (43, 45, 49)
EMBED_TITLE_TEXT = (0, 168, 252)

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
    preserve_whitespace: bool = False,
) -> str:
    """Bound chat text while retaining Unicode emoji and optional code spacing."""
    content = content[:MAX_SOURCE_TEXT_LENGTH]
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _CUSTOM_EMOJI.sub(r":\1:", content)
    if preserve_whitespace:
        normalized = content.strip("\n")
    else:
        lines: list[str] = []
        for raw_line in content.split("\n"):
            line = _HORIZONTAL_WHITESPACE.sub(" ", raw_line).strip()
            if line:
                lines.append(line)
            elif lines and lines[-1] != "":
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        normalized = "\n".join(lines)
    if not normalized.strip():
        if allow_empty:
            return ""
        raise ValueError(
            "Tin nhắn không có chữ hoặc ảnh để tạo highlight."
        ) from None
    if len(normalized) > MAX_NORMALIZED_TEXT_LENGTH:
        normalized = normalized[:MAX_NORMALIZED_TEXT_LENGTH - 1].rstrip() + "…"
    return normalized


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
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    return _placeholder_avatar(display_name, accent)
                return ImageOps.fit(
                    source.convert("RGB"),
                    (AVATAR_SIZE, AVATAR_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
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
            if source.width * source.height > MAX_IMAGE_PIXELS:
                return None
            # The first frame gives animated GIF/WebP embeds a stable PNG preview.
            source.load()
            frame = ImageOps.exif_transpose(source)
            frame = _contain_image(frame, max_width, max_height)
            frame = frame.convert("RGBA")
            background = Image.new("RGBA", frame.size, BACKGROUND)
            frame = Image.alpha_composite(background, frame).convert("RGB")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    return frame


def _contain_image(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """Keep extreme aspect ratios valid even when one dimension rounds below 1px."""
    scale = min(max_width / image.width, max_height / image.height)
    size = (
        max(1, min(max_width, round(image.width * scale))),
        max(1, min(max_height, round(image.height * scale))),
    )
    return image.resize(size, Image.Resampling.LANCZOS)


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


def _fit_lines(
    text: str,
    font: _FallbackFont,
    width: int,
    height: int,
    spacing: int,
) -> list[str]:
    lines = wrap_quote_text(text, font, width) if text else []
    truncated = False
    while lines and _text_block_height(lines, font, spacing) > height:
        lines.pop()
        truncated = True
    if truncated and lines:
        lines[-1] = _truncate_to_width(lines[-1], font, width, force_suffix=True)
    return lines


def _render_gallery(images: list[Image.Image], max_height: int) -> Image.Image:
    """Preserve the full image in each tile, including tall or wide attachments."""
    if len(images) == 1:
        return _contain_image(images[0], TEXT_MAX_WIDTH, max_height)
    columns = 2
    rows = (len(images) + columns - 1) // columns
    tile_width = (TEXT_MAX_WIDTH - GALLERY_GAP) // columns
    tile_height = max(1, (max_height - GALLERY_GAP * (rows - 1)) // rows)
    tiles = [
        _contain_image(image, tile_width, tile_height)
        for image in images
    ]
    row_heights = [
        max(tile.height for tile in tiles[start:start + columns])
        for start in range(0, len(tiles), columns)
    ]
    gallery = Image.new(
        "RGB", (TEXT_MAX_WIDTH, sum(row_heights) + GALLERY_GAP * (rows - 1)),
        BACKGROUND,
    )
    y = 0
    for row, row_height in enumerate(row_heights):
        for column, tile in enumerate(tiles[row * columns:(row + 1) * columns]):
            x = column * (tile_width + GALLERY_GAP) + (tile_width - tile.width) // 2
            _paste_rounded(gallery, tile, (x, y), IMAGE_CORNER_RADIUS)
        y += row_height + GALLERY_GAP
    return gallery


def _embed_text(value: str) -> str:
    return normalize_highlight_text(value, allow_empty=True, preserve_whitespace=True)


def _render_embed_panel(
    embed: HighlightEmbed,
    main_image: Image.Image | None,
    thumbnail: Image.Image | None,
    max_height: int,
) -> Image.Image | None:
    """Fit an embed's text and media inside one bounded Discord-style panel."""
    body_font = EmbedTextFont(EMBED_FONT_SIZE)
    bold_font = EmbedTextFont(EMBED_FONT_SIZE, bold=True)
    title_font = EmbedTextFont(EMBED_TITLE_FONT_SIZE, bold=True)
    meta_font = EmbedTextFont(EMBED_META_FONT_SIZE)
    inner_width = TEXT_MAX_WIDTH - 2 * EMBED_PADDING
    inner_height = max_height - 2 * EMBED_PADDING
    text_width = inner_width
    if thumbnail is not None:
        text_width -= EMBED_THUMBNAIL_SIZE + EMBED_GAP

    # A block remains independently styled when it is shortened to make room.
    blocks: list[tuple[list[TextLine], EmbedTextFont, tuple[int, int, int]]] = []
    sections = [
        (embed.author_name, meta_font, BODY_TEXT),
        (embed.title, title_font, EMBED_TITLE_TEXT),
        (embed.description, body_font, BODY_TEXT),
    ]
    for name, value in embed.fields[:25]:
        sections.extend(((name, bold_font, BODY_TEXT), (value, body_font, BODY_TEXT)))
    for value, font, color in sections:
        text = _embed_text(value)
        if text:
            blocks.append((wrap_embed_text(text, font, text_width), font, color))

    footer_text = _embed_text(embed.footer_text)
    footer_lines = wrap_embed_text(footer_text, meta_font, inner_width) if footer_text else []
    footer_truncated = False
    while footer_lines and _text_block_height(footer_lines, meta_font, EMBED_GAP) > min(60, inner_height // 4):
        footer_lines.pop()
        footer_truncated = True
    if footer_truncated and footer_lines:
        footer_lines[-1] = truncate_embed_line(footer_lines[-1], meta_font, inner_width)
    footer_height = (
        _text_block_height(footer_lines, meta_font, EMBED_GAP)
        if footer_lines else 0
    )
    footer_space = footer_height + EMBED_GAP if footer_lines else 0
    image_reserve = (
        min(main_image.height, max(MIN_IMAGE_HEIGHT, inner_height // 3)) + EMBED_GAP
        if main_image is not None else 0
    )
    text_budget = max(0, inner_height - footer_space - image_reserve)

    block_heights = [
        _text_block_height(lines, font, EMBED_GAP)
        for lines, font, _ in blocks
    ]

    def blocks_height() -> int:
        return sum(block_heights) + EMBED_GAP * max(0, len(blocks) - 1)

    shortened: set[int] = set()
    while blocks and blocks_height() > text_budget:
        longest = max(range(len(blocks)), key=lambda index: len(blocks[index][0]))
        if len(blocks[longest][0]) > 1:
            lines, font, _ = blocks[longest]
            line_step = max(1, font.primary.getbbox("A")[3] + EMBED_GAP)
            overflow = blocks_height() - text_budget
            remove_count = min(
                len(lines) - 1, max(1, (overflow + line_step - 1) // line_step),
            )
            del lines[-remove_count:]
            block_heights[longest] = _text_block_height(lines, font, EMBED_GAP)
            shortened.add(longest)
        else:
            blocks.pop()
            block_heights.pop()
            if blocks:
                shortened.add(len(blocks) - 1)
    for index in shortened:
        if index < len(blocks):
            lines, font, _ = blocks[index]
            lines[-1] = truncate_embed_line(lines[-1], font, text_width)

    if thumbnail is not None:
        thumbnail = _contain_image(
            thumbnail,
            EMBED_THUMBNAIL_SIZE,
            max(1, min(EMBED_THUMBNAIL_SIZE, text_budget)),
        )
    top_height = max(blocks_height(), thumbnail.height if thumbnail is not None else 0)
    if main_image is not None:
        image_budget = max(1, inner_height - top_height - footer_space - EMBED_GAP)
        main_image = _contain_image(main_image, inner_width, image_budget)
    image_space = (
        main_image.height + (EMBED_GAP if top_height else 0)
        if main_image is not None else 0
    )
    if not blocks and thumbnail is None and main_image is None and not footer_lines:
        return None
    height = 2 * EMBED_PADDING + top_height + image_space + footer_space
    panel = Image.new("RGB", (TEXT_MAX_WIDTH, height), EMBED_BACKGROUND)
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, 5, height), fill=embed.color_rgb or DEFAULT_ACCENT)

    y = EMBED_PADDING
    for lines, font, color in blocks:
        top = font.multiline_bbox(lines, EMBED_GAP)[1]
        font.draw_multiline(draw, (EMBED_PADDING, y - top), lines, color, EMBED_GAP)
        y += _text_block_height(lines, font, EMBED_GAP) + EMBED_GAP
    if thumbnail is not None:
        _paste_rounded(
            panel, thumbnail,
            (TEXT_MAX_WIDTH - EMBED_PADDING - thumbnail.width, EMBED_PADDING), 6,
        )
    y = EMBED_PADDING + top_height
    if main_image is not None:
        if top_height:
            y += EMBED_GAP
        _paste_rounded(panel, main_image, (EMBED_PADDING, y), 6)
        y += main_image.height
    if footer_lines:
        y += EMBED_GAP
        top = meta_font.multiline_bbox(footer_lines, EMBED_GAP)[1]
        meta_font.draw_multiline(
            draw, (EMBED_PADDING, y - top), footer_lines, MUTED_TEXT, EMBED_GAP,
        )
    return panel


def render_highlight_card(
    *,
    avatar_bytes: bytes | None,
    display_name: str,
    channel_name: str,
    message_text: str,
    timestamp_label: str,
    accent_rgb: tuple[int, int, int] | None = None,
    attachment_bytes: bytes | None = None,
    attachment_images: list[bytes] | None = None,
    embeds: list[HighlightEmbed] | None = None,
) -> bytes:
    """Render message text, image attachments, and rich embeds as a bounded PNG."""
    text = normalize_highlight_text(message_text, allow_empty=True)
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

    image_sources = list(attachment_images or [])
    if attachment_bytes is not None:
        image_sources.insert(0, attachment_bytes)
    images = [
        image for data in image_sources[:MAX_HIGHLIGHT_IMAGES]
        if (image := _prepare_attachment_image(data, TEXT_MAX_WIDTH, IMAGE_MAX_HEIGHT))
        is not None
    ]
    prepared_embeds = []
    for embed in (embeds or [])[:MAX_HIGHLIGHT_EMBEDS]:
        main_image = _prepare_attachment_image(
            embed.image_bytes, TEXT_MAX_WIDTH - 2 * EMBED_PADDING, IMAGE_MAX_HEIGHT,
        )
        thumbnail = _prepare_attachment_image(
            embed.thumbnail_bytes, EMBED_THUMBNAIL_SIZE, EMBED_THUMBNAIL_SIZE,
        )
        text_values = [embed.author_name, embed.title, embed.description, embed.footer_text]
        text_values.extend(value for pair in embed.fields[:25] for value in pair)
        if main_image is not None or thumbnail is not None or any(
            _embed_text(value) for value in text_values
        ):
            prepared_embeds.append((embed, main_image, thumbnail))
    block_count = bool(images) + len(prepared_embeds)
    if not text and not block_count:
        raise ValueError("Tin nhắn không có chữ hoặc ảnh để tạo highlight.")

    body_budget = (
        MAX_CARD_HEIGHT - HEADER_HEIGHT - 2 * PADDING - name_height - TEXT_TOP_GAP
    )
    # Long captions cannot consume the area reserved for attachments and embeds.
    text_budget = min(body_budget, 260) if block_count else body_budget
    lines = _fit_lines(text, body_font, TEXT_MAX_WIDTH, text_budget, LINE_SPACING)
    text_height = _text_block_height(lines, body_font, LINE_SPACING) if lines else 0
    media: list[Image.Image] = []
    if block_count:
        block_budget = (
            body_budget - text_height - IMAGE_TOP_GAP * block_count
        ) // block_count
        if images:
            media.append(_render_gallery(images, min(IMAGE_MAX_HEIGHT, block_budget)))
        for embed, main_image, thumbnail in prepared_embeds:
            panel = _render_embed_panel(embed, main_image, thumbnail, block_budget)
            if panel is not None:
                media.append(panel)
    image_height = (
        sum(item.height for item in media) + IMAGE_TOP_GAP * (len(media) - 1)
        if media else 0
    )
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

    for index, item in enumerate(media):
        if lines or index:
            content_y += IMAGE_TOP_GAP
        _paste_rounded(
            card,
            item,
            (text_x, content_y),
            IMAGE_CORNER_RADIUS,
        )
        content_y += item.height

    output = BytesIO()
    card.save(output, format="PNG", optimize=True)
    return output.getvalue()
