"""Text preparation and PNG rendering for Discord quote cards."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import math
from pathlib import Path
import re
from threading import local
from typing import Protocol
import unicodedata

import emoji
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


CARD_WIDTH = 1200
CARD_HEIGHT = 675
AVATAR_SIZE = 224
TEXT_AREA_WIDTH = 720
TEXT_AREA_HEIGHT = 340
TEXT_FONT_SIZES = (50, 44, 38, 32)
DEFAULT_ACCENT = (88, 101, 242)
MAX_SOURCE_TEXT_LENGTH = 4_000
MAX_NORMALIZED_TEXT_LENGTH = 1_200

_CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_]{2,32}):\d+>")
_HORIZONTAL_WHITESPACE = re.compile(r"[\t\f\v ]+")
_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_FONT_PATH = _ROOT / "fonts" / "NotoSans-Variable.ttf"
BUNDLED_EMOJI_FONT_PATH = _ROOT / "fonts" / "NotoEmoji-Variable.ttf"
BUNDLED_SYMBOL_FONT_PATH = (
    _ROOT / "fonts" / "NotoSansSymbols-Variable.ttf"
)
BUNDLED_SYMBOLS2_FONT_PATH = (
    _ROOT / "fonts" / "NotoSansSymbols2-Regular.ttf"
)

_FALLBACK_FONT_PATHS = (
    BUNDLED_EMOJI_FONT_PATH,
    BUNDLED_SYMBOL_FONT_PATH,
    BUNDLED_SYMBOLS2_FONT_PATH,
)
_MISSING_GLYPH_PROBE = "\U0010ffff"
_UNSUPPORTED_GLYPH = "\N{REPLACEMENT CHARACTER}"
_EMOJI_VARIATION_SELECTORS = {"\ufe0e", "\ufe0f"}
_MAX_GLYPH_SUPPORT_CACHE = 8_192
_FONT_CACHE = local()

_REGULAR_FONT_CANDIDATES = (
    BUNDLED_FONT_PATH,
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)
_BOLD_FONT_CANDIDATES = (
    BUNDLED_FONT_PATH,
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
)


class MeasurableFont(Protocol):
    def getlength(self, text: str) -> float: ...


class _FallbackFont:
    """Measure and draw text with bundled glyph-by-glyph fallbacks."""

    def __init__(
        self,
        primary: ImageFont.FreeTypeFont,
        fallbacks: tuple[ImageFont.FreeTypeFont, ...],
    ) -> None:
        self.primary = primary
        self.fonts = (primary, *fallbacks)
        # RAQM can hide missing selectors or add dotted circles to marks. Probe
        # unshaped glyphs so coverage does not depend on the rendering engine.
        self._probe_fonts = tuple(
            font if font.layout_engine == ImageFont.Layout.BASIC
            else ImageFont.truetype(
                BytesIO(font.font_bytes) if hasattr(font, "font_bytes") else font.path,
                size=font.size,
                index=font.index,
                encoding=font.encoding,
                layout_engine=ImageFont.Layout.BASIC,
            )
            for font in self.fonts
        )
        self._missing_signatures = tuple(
            self._glyph_signature(font, _MISSING_GLYPH_PROBE)
            for font in self._probe_fonts
        )
        self._support_cache: dict[tuple[int, str], bool] = {}

    @staticmethod
    def _glyph_signature(
        font: ImageFont.FreeTypeFont,
        character: str,
    ) -> tuple[tuple[int, int], bytes]:
        mask = font.getmask(character)
        return mask.size, bytes(mask)

    def _supports(self, font_index: int, character: str) -> bool:
        key = (font_index, character)
        if key not in self._support_cache:
            if len(self._support_cache) >= _MAX_GLYPH_SUPPORT_CACHE:
                self._support_cache.clear()
            signature = self._glyph_signature(
                self._probe_fonts[font_index],
                character,
            )
            self._support_cache[key] = (
                signature != self._missing_signatures[font_index]
            )
        return self._support_cache[key]

    def _font_runs(
        self,
        text: str,
    ) -> list[tuple[str, ImageFont.FreeTypeFont]]:
        runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
        current_font = self.primary
        current_text = ""

        for character in text:
            category = unicodedata.category(character)
            if character.isspace():
                selected_font = self.primary
                rendered_character = character
            elif category.startswith("M") or category == "Cf":
                # Keep combining marks and joiners attached to the previous glyph.
                selected_font = current_font
                rendered_character = character
                current_index = self.fonts.index(current_font)
                if not self._supports(current_index, character):
                    # Unsupported invisible modifiers must not leak a .notdef box.
                    continue
            else:
                selected_font = self.primary
                rendered_character = character
                for index, candidate in enumerate(self.fonts):
                    if self._supports(index, character):
                        selected_font = candidate
                        break
                else:
                    rendered_character = _UNSUPPORTED_GLYPH

            if current_text and selected_font is not current_font:
                runs.append((current_text, current_font))
                current_text = ""
            current_font = selected_font
            current_text += rendered_character

        if current_text:
            runs.append((current_text, current_font))
        return runs

    def getlength(self, text: str) -> float:
        return sum(
            font.getlength(run)
            for run, font in self._font_runs(text)
        )

    def _text_bbox(self, text: str) -> tuple[float, float, float, float]:
        runs = self._font_runs(text)
        if not runs:
            return tuple(float(value) for value in self.primary.getbbox(""))

        left = 0.0
        baseline = float(self.primary.getmetrics()[0])
        bbox: tuple[float, float, float, float] | None = None
        for run, font in runs:
            run_box = font.getbbox(run, anchor="ls")
            positioned = (
                left + run_box[0],
                baseline + run_box[1],
                left + run_box[2],
                baseline + run_box[3],
            )
            if bbox is None:
                bbox = positioned
            else:
                bbox = (
                    min(bbox[0], positioned[0]),
                    min(bbox[1], positioned[1]),
                    max(bbox[2], positioned[2]),
                    max(bbox[3], positioned[3]),
                )
            left += font.getlength(run)

        assert bbox is not None
        return bbox

    def multiline_bbox(
        self,
        lines: list[str],
        spacing: int,
    ) -> tuple[float, float, float, float]:
        line_spacing = self.primary.getbbox("A")[3] + spacing
        bbox: tuple[float, float, float, float] | None = None
        for index, line in enumerate(lines):
            line_box = self._text_bbox(line)
            top_offset = index * line_spacing
            positioned = (
                line_box[0],
                line_box[1] + top_offset,
                line_box[2],
                line_box[3] + top_offset,
            )
            if bbox is None:
                bbox = positioned
            else:
                bbox = (
                    min(bbox[0], positioned[0]),
                    min(bbox[1], positioned[1]),
                    max(bbox[2], positioned[2]),
                    max(bbox[3], positioned[3]),
                )

        return bbox or (0.0, 0.0, 0.0, 0.0)

    def draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: str,
        fill: tuple[int, int, int],
    ) -> None:
        x, y = xy
        baseline = y + self.primary.getmetrics()[0]
        for run, font in self._font_runs(text):
            draw.text(
                (x, baseline),
                run,
                font=font,
                fill=fill,
                anchor="ls",
            )
            x += font.getlength(run)

    def draw_multiline(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        lines: list[str],
        fill: tuple[int, int, int],
        spacing: int,
    ) -> None:
        line_spacing = self.primary.getbbox("A")[3] + spacing
        for index, line in enumerate(lines):
            self.draw_text(
                draw,
                (xy[0], xy[1] + index * line_spacing),
                line,
                fill,
            )


def _bundled_emoji_supports(character: str) -> bool:
    """Return whether the bundled emoji font contains one base glyph."""
    state = getattr(_FONT_CACHE, "emoji_coverage", None)
    if state is None:
        if not BUNDLED_EMOJI_FONT_PATH.is_file():
            return False
        font = ImageFont.truetype(
            str(BUNDLED_EMOJI_FONT_PATH), size=32,
            layout_engine=ImageFont.Layout.BASIC,
        )
        missing_signature = _FallbackFont._glyph_signature(
            font,
            _MISSING_GLYPH_PROBE,
        )
        state = (font, missing_signature, {})
        _FONT_CACHE.emoji_coverage = state

    font, missing_signature, support_cache = state
    supported = support_cache.get(character)
    if supported is None:
        supported = (
            _FallbackFont._glyph_signature(font, character)
            != missing_signature
        )
        support_cache[character] = supported
    return supported


def _prepare_unicode_emoji(content: str) -> str:
    """Keep simple glyphs and demojize sequences requiring text shaping."""
    matches = emoji.emoji_list(content)
    if not matches:
        return content

    prepared: list[str] = []
    cursor = 0
    for match in matches:
        start = match["match_start"]
        end = match["match_end"]
        value = match["emoji"]
        prepared.append(content[cursor:start])
        base_characters = [
            character
            for character in value
            if character not in _EMOJI_VARIATION_SELECTORS
        ]
        if (
            len(base_characters) == 1
            and _bundled_emoji_supports(base_characters[0])
        ):
            prepared.append(value)
        else:
            prepared.append(emoji.demojize(value))
        cursor = end

    prepared.append(content[cursor:])
    return "".join(prepared)


def normalize_quote_text(content: str) -> str:
    """Prepare Discord message text for a readable, bounded quote card."""
    content = content[:MAX_SOURCE_TEXT_LENGTH]
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _CUSTOM_EMOJI.sub(r":\1:", content)
    content = _prepare_unicode_emoji(content)

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
    if not normalized:
        raise ValueError("Tin nhắn không có nội dung chữ để tạo quote.")
    if len(normalized) > MAX_NORMALIZED_TEXT_LENGTH:
        normalized = (
            normalized[: MAX_NORMALIZED_TEXT_LENGTH - 1].rstrip() + "…"
        )
    return normalized


def _longest_fitting_prefix(
    text: str,
    font: MeasurableFont,
    max_width: int,
) -> int:
    low = 1
    high = len(text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        if font.getlength(text[:middle]) <= max_width:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return max(1, best)


def wrap_quote_text(
    text: str,
    font: MeasurableFont,
    max_width: int,
) -> list[str]:
    """Wrap text by rendered width, including words wider than one line."""
    if max_width <= 0:
        raise ValueError("max_width must be positive")

    wrapped: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            if wrapped and wrapped[-1] != "":
                wrapped.append("")
            continue

        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= max_width:
                current = candidate
                continue

            if current:
                wrapped.append(current)
                current = ""

            fragments: list[str] = []
            fragment = ""
            for character in word:
                candidate = fragment + character
                if fragment and font.getlength(candidate) > max_width:
                    fragments.append(fragment)
                    fragment = character
                else:
                    fragment = candidate
            if fragment:
                fragments.append(fragment)

            if fragments:
                wrapped.extend(fragments[:-1])
                current = fragments[-1]

        if current:
            wrapped.append(current)

    return wrapped or [""]


def _truncate_to_width(
    text: str,
    font: MeasurableFont,
    max_width: int,
    suffix: str = "…",
    *,
    force_suffix: bool = False,
) -> str:
    if not force_suffix and font.getlength(text) <= max_width:
        return text
    if force_suffix and font.getlength(text + suffix) <= max_width:
        return text.rstrip() + suffix

    available = max_width - int(font.getlength(suffix))
    if available <= 0:
        return suffix
    prefix_length = _longest_fitting_prefix(text, font, available)
    return text[:prefix_length].rstrip() + suffix


@lru_cache(maxsize=2)
def _font_path(bold: bool) -> str | None:
    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _font_path(bold)
    if path is not None:
        font = ImageFont.truetype(path, size=size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except OSError:
                # Static bold font candidates already contain the right weight.
                pass
        return font
    return ImageFont.load_default(size=size)


def _load_fallback_font(size: int, *, bold: bool = False) -> _FallbackFont:
    cache: dict[tuple[int, bool], _FallbackFont] | None = getattr(
        _FONT_CACHE,
        "fallback_fonts",
        None,
    )
    if cache is None:
        cache = {}
        _FONT_CACHE.fallback_fonts = cache

    key = (size, bold)
    cached = cache.get(key)
    if cached is not None:
        return cached

    fallbacks: list[ImageFont.FreeTypeFont] = []
    for path in _FALLBACK_FONT_PATHS:
        if not path.is_file():
            continue
        font = ImageFont.truetype(str(path), size=size)
        if bold:
            try:
                font.set_variation_by_name("Bold")
            except OSError:
                # Static fallback fonts do not expose weight variations.
                pass
        fallbacks.append(font)
    loaded = _FallbackFont(_load_font(size, bold=bold), tuple(fallbacks))
    cache[key] = loaded
    return loaded


def _text_block_height(
    lines: list[str],
    font: _FallbackFont,
    spacing: int,
) -> int:
    box = font.multiline_bbox(lines, spacing)
    return max(1, math.ceil(box[3] - box[1]))


def _fit_quote_lines(text: str) -> tuple[_FallbackFont, list[str], int]:
    selected_font = _load_fallback_font(TEXT_FONT_SIZES[-1])
    selected_lines: list[str] = []
    selected_spacing = 10

    for size in TEXT_FONT_SIZES:
        font = _load_fallback_font(size)
        spacing = max(8, size // 4)
        lines = wrap_quote_text(text, font, TEXT_AREA_WIDTH)
        selected_font = font
        selected_lines = lines
        selected_spacing = spacing
        if _text_block_height(lines, font, spacing) <= TEXT_AREA_HEIGHT:
            return font, lines, spacing

    low = 1
    high = len(selected_lines)
    fitting_line_count = 1
    while low <= high:
        middle = (low + high) // 2
        if (
            _text_block_height(
                selected_lines[:middle],
                selected_font,
                selected_spacing,
            )
            <= TEXT_AREA_HEIGHT
        ):
            fitting_line_count = middle
            low = middle + 1
        else:
            high = middle - 1

    was_truncated = fitting_line_count < len(selected_lines)
    selected_lines = selected_lines[:fitting_line_count]

    while len(selected_lines) > 1 and not selected_lines[-1]:
        selected_lines.pop()
        was_truncated = True
    if was_truncated:
        selected_lines[-1] = _truncate_to_width(
            selected_lines[-1],
            selected_font,
            TEXT_AREA_WIDTH,
            force_suffix=True,
        )
    return selected_font, selected_lines, selected_spacing


def _mix(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    return tuple(
        round(first[index] * (1 - ratio) + second[index] * ratio)
        for index in range(3)
    )


def _safe_accent(accent_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    accent = tuple(max(0, min(255, int(value))) for value in accent_rgb)
    if sum(accent) < 90:
        accent = DEFAULT_ACCENT
    luminance = (
        0.2126 * accent[0]
        + 0.7152 * accent[1]
        + 0.0722 * accent[2]
    )
    if luminance < 135:
        lighten_by = (135 - luminance) / (255 - luminance)
        accent = _mix(accent, (255, 255, 255), lighten_by)
    return accent


def _draw_background(
    card: Image.Image,
    accent: tuple[int, int, int],
) -> None:
    draw = ImageDraw.Draw(card)
    start = _mix((20, 23, 34), accent, 0.18)
    end = (8, 10, 17)
    for y in range(CARD_HEIGHT):
        ratio = y / (CARD_HEIGHT - 1)
        color = _mix(start, end, ratio)
        draw.line((0, y, CARD_WIDTH, y), fill=color)

    draw.rounded_rectangle(
        (328, 48, 1140, 627),
        radius=34,
        fill=(20, 23, 34),
        outline=_mix((65, 70, 88), accent, 0.18),
        width=2,
    )
    draw.rounded_rectangle(
        (328, 48, 338, 627),
        radius=5,
        fill=accent,
    )


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
    font = _load_font(88, bold=True)
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


def _single_line(value: str, fallback: str) -> str:
    value = _prepare_unicode_emoji(value.replace("\n", " "))
    normalized = _HORIZONTAL_WHITESPACE.sub(" ", value).strip()
    return normalized or fallback


def render_quote_card(
    *,
    avatar_bytes: bytes | None,
    display_name: str,
    username: str,
    quote_text: str,
    context_label: str,
    accent_rgb: tuple[int, int, int] = DEFAULT_ACCENT,
) -> bytes:
    """Render one fixed-size PNG quote card and return its bytes."""
    text = normalize_quote_text(quote_text)
    display_name = _single_line(display_name, "Discord user")
    username = _single_line(username, display_name)
    context_label = _single_line(context_label, "Discord")
    accent = _safe_accent(accent_rgb)

    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT))
    _draw_background(card, accent)
    draw = ImageDraw.Draw(card)

    avatar = _prepare_avatar(avatar_bytes, display_name, accent)
    avatar_x = 68
    avatar_y = (CARD_HEIGHT - AVATAR_SIZE) // 2
    border = 7
    draw.ellipse(
        (
            avatar_x - border,
            avatar_y - border,
            avatar_x + AVATAR_SIZE + border,
            avatar_y + AVATAR_SIZE + border,
        ),
        fill=accent,
    )
    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255)
    card.paste(avatar, (avatar_x, avatar_y), mask)

    quote_font, lines, spacing = _fit_quote_lines(text)
    quote_box = quote_font.multiline_bbox(lines, spacing)
    block_height = quote_box[3] - quote_box[1]
    quote_y = 88 + max(0, (TEXT_AREA_HEIGHT - block_height) // 2)

    mark_font = _load_font(96, bold=True)
    draw.text((356, 42), "“", font=mark_font, fill=_mix((70, 75, 94), accent, 0.45))
    quote_font.draw_multiline(
        draw,
        (382, quote_y),
        lines,
        (244, 245, 250),
        spacing,
    )

    name_font = _load_fallback_font(38, bold=True)
    username_font = _load_fallback_font(25)
    context_font = _load_fallback_font(22)
    safe_name = _truncate_to_width(
        display_name,
        name_font,
        TEXT_AREA_WIDTH,
    )
    safe_username = _truncate_to_width(
        f"@{username}",
        username_font,
        TEXT_AREA_WIDTH,
    )
    safe_context = _truncate_to_width(
        context_label,
        context_font,
        TEXT_AREA_WIDTH,
    )

    name_font.draw_text(draw, (382, 493), safe_name, accent)
    username_font.draw_text(
        draw,
        (382, 540),
        safe_username,
        (174, 179, 195),
    )
    context_font.draw_text(
        draw,
        (382, 586),
        safe_context,
        (126, 132, 151),
    )

    output = BytesIO()
    card.save(output, format="PNG", optimize=True)
    return output.getvalue()
