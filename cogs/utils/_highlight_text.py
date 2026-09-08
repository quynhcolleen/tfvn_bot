"""Bounded Discord embed Markdown parsing and styled text layout."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
import unicodedata

from PIL import Image, ImageDraw

from cogs.utils._highlight_font import load_highlight_font
from cogs.utils._quote_card import _FallbackFont


CODE_BACKGROUND = (30, 31, 34)
LINK_COLOR = (0, 168, 252)
_DELIMITERS = (
    ("***", {"bold": True, "italic": True}),
    ("**", {"bold": True}),
    ("__", {"underline": True}),
    ("~~", {"strike": True}),
    ("*", {"italic": True}),
    ("_", {"italic": True}),
)
_LINK = re.compile(r"\[((?:\\.|[^\]\\\n])+)\]\((https?://(?:\\.|[^()\s]|\([^()\s]*\))+)\)")
_URL = re.compile(r"https?://[^\s<>]+")
_ESCAPABLE = frozenset(r"\`*_{}[]()#+-.!>|~")


@dataclass(frozen=True)
class TextStyle:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    link: bool = False


@dataclass(frozen=True)
class TextRun:
    text: str
    style: TextStyle = TextStyle()


@dataclass(frozen=True)
class TextLine:
    runs: tuple[TextRun, ...] = ()

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


@dataclass
class _ParseBudget:
    remaining: int


def _append(runs: list[TextRun], text: str, style: TextStyle) -> None:
    if not text:
        return
    if runs and runs[-1].style == style:
        runs[-1] = TextRun(runs[-1].text + text, style)
    else:
        runs.append(TextRun(text, style))


def _can_open_delimiter(text: str, position: int, delimiter: str) -> bool:
    start = position + len(delimiter)
    return (
        text.startswith(delimiter, position)
        and start < len(text) and not text[start].isspace()
        and not (delimiter.startswith("_") and position and text[position - 1].isalnum())
    )


def _find_delimiter_end(
    text: str, start: int, delimiter: str, depth: int,
    cache: dict[tuple[int, str, int], int], budget: _ParseBudget,
) -> int:
    key = (start, delimiter, depth)
    if key not in cache:
        cache[key] = _scan_delimiter_end(text, start, delimiter, depth, cache, budget)
    return cache[key]


def _scan_delimiter_end(
    text: str, start: int, delimiter: str, depth: int,
    cache: dict[tuple[int, str, int], int], budget: _ParseBudget,
) -> int:
    """Skip literal spans and balanced nested markers when finding a closer."""
    if depth >= 8:
        return -1
    position = start
    while position < len(text):
        if budget.remaining <= 0:
            return -1
        budget.remaining -= 1
        character = text[position]
        if character == "\\" and position + 1 < len(text) and text[position + 1] in _ESCAPABLE:
            position += 2
            continue
        if character == "`":
            fence = re.match(r"`+", text[position:])[0]
            end = text.find(fence, position + len(fence))
            position = end + len(fence) if end != -1 else position + len(fence)
            continue
        link = _LINK.match(text, position)
        if link:
            position = link.end()
            continue
        url = _URL.match(text, position)
        if url:
            # A URL can end at the enclosing style's markers without whitespace.
            position += len(url[0].rstrip(delimiter[0]))
            continue
        if character not in "*_~":
            position += 1
            continue
        run_end = position + 1
        while run_end < len(text) and text[run_end] == character:
            run_end += 1
        after = position + len(delimiter)
        can_close = (
            text.startswith(delimiter, position)
            and position > start and not text[position - 1].isspace()
            and not (delimiter.startswith("_") and after < len(text) and text[after].isalnum())
        )
        if can_close and run_end - position == len(delimiter):
            return position
        # A longer opening run belongs to a nested style. At a combined closing
        # run (e.g. ***), the inner span consumes its markers before the outer.
        nested_end = -1
        for nested, _ in _DELIMITERS:
            if nested == delimiter or not _can_open_delimiter(text, position, nested):
                continue
            if can_close and len(nested) <= len(delimiter):
                continue
            end = _find_delimiter_end(
                text, position + len(nested), nested, depth + 1, cache, budget,
            )
            if end != -1:
                nested_end = end + len(nested)
                break
        if nested_end != -1:
            position = nested_end
        elif can_close:
            return position
        else:
            position = run_end
    return -1


def parse_embed_text(
    text: str, style: TextStyle = TextStyle(), _depth: int = 0,
    _budget: _ParseBudget | None = None,
) -> tuple[TextRun, ...]:
    """Keep code literal and turn supported Markdown delimiters into style runs."""
    if _depth >= 8:
        return (TextRun(text, style),)
    if _budget is None:
        # Pathological unmatched nesting must not monopolize card rendering.
        _budget = _ParseBudget(max(64, len(text) * 8))
    runs: list[TextRun] = []
    delimiter_ends: dict[tuple[int, str, int], int] = {}
    position = 0
    while position < len(text):
        if _budget.remaining <= 0:
            _append(runs, text[position:], style)
            break
        character = text[position]
        if character == "\\" and position + 1 < len(text) and text[position + 1] in _ESCAPABLE:
            _append(runs, text[position + 1], style)
            position += 2
            continue
        if character == "`":
            fence = re.match(r"`+", text[position:])[0]
            block = len(fence) >= 3
            end = text.find(fence, position + len(fence))
            if end != -1:
                content = text[position + len(fence):end]
                if block:
                    content = re.sub(r"^[A-Za-z0-9_+.-]*\n", "", content, count=1)
                    content = content.removesuffix("\n")
                    if runs and not runs[-1].text.endswith("\n"):
                        _append(runs, "\n", style)
                _append(runs, content, TextStyle(code=True))
                position = end + len(fence)
                if block and position < len(text) and text[position] != "\n":
                    _append(runs, "\n", style)
                continue
            _append(runs, fence, style)
            position += len(fence)
            continue
        link = _LINK.match(text, position)
        if link:
            for run in parse_embed_text(link[1], replace(style, link=True), _depth + 1, _budget):
                _append(runs, run.text, run.style)
            position = link.end()
            continue
        url = _URL.match(text, position)
        if url:
            _append(runs, url[0], replace(style, link=True))
            position = url.end()
            continue
        matched = False
        for delimiter, changes in _DELIMITERS:
            start = position + len(delimiter)
            if not _can_open_delimiter(text, position, delimiter):
                continue
            end = _find_delimiter_end(text, start, delimiter, _depth, delimiter_ends, _budget)
            if end == -1:
                continue
            for run in parse_embed_text(
                text[start:end], replace(style, **changes), _depth + 1, _budget,
            ):
                _append(runs, run.text, run.style)
            position = end + len(delimiter)
            matched = True
            break
        if not matched:
            _append(runs, character, style)
            position += 1
    return tuple(runs)


class EmbedTextFont:
    """Use the same style metrics for wrapping, clipping, and drawing an embed."""

    def __init__(self, size: int, *, bold: bool = False) -> None:
        self.regular = load_highlight_font(size, bold=bold)
        self.bold = load_highlight_font(size, bold=True)
        self.primary = self.regular.primary
        self._italic_padding = math.ceil(sum(self.primary.getmetrics()) * 0.2)
        self._code_cell = self.regular.getlength("0")

    @staticmethod
    def _code_glyphs(text: str) -> list[str]:
        """Keep combining marks and joined emoji in one code cell."""
        glyphs: list[str] = []
        for character in text:
            if glyphs and (
                unicodedata.category(character).startswith("M")
                or character == "\u200d"
                or glyphs[-1].endswith("\u200d")
            ):
                glyphs[-1] += character
            else:
                glyphs.append(character)
        return glyphs

    def _font(self, style: TextStyle) -> _FallbackFont:
        return self.bold if style.bold else self.regular

    def _run_width(self, run: TextRun) -> float:
        if run.style.code:
            return 8 + sum(
                max(self._code_cell, self.regular.getlength(glyph))
                for glyph in self._code_glyphs(run.text)
            )
        return self._font(run.style).getlength(run.text) + (
            self._italic_padding if run.style.italic else 0
        )

    def getlength(self, line: TextLine) -> float:
        return sum(self._run_width(run) for run in line.runs)

    def _bbox(self, line: TextLine) -> tuple[float, float, float, float]:
        if not line.runs:
            return (0, 0, 0, 0)
        boxes = []
        for run in line.runs:
            box = self._font(run.style)._text_bbox(run.text)
            if run.style.code or run.style.italic:
                box = (0, 0, 0, sum(self.primary.getmetrics()))
            elif run.style.underline:
                box = (box[0], box[1], box[2], max(box[3], self.primary.getmetrics()[0] + 3))
            boxes.append(box)
        return (0, min(box[1] for box in boxes), self.getlength(line), max(box[3] for box in boxes))

    def multiline_bbox(self, lines: list[TextLine], spacing: int) -> tuple[float, float, float, float]:
        step = self.primary.getbbox("A")[3] + spacing
        boxes = [self._bbox(line) for line in lines]
        return (
            0,
            min((box[1] + i * step for i, box in enumerate(boxes)), default=0),
            max((box[2] for box in boxes), default=0),
            max((box[3] + i * step for i, box in enumerate(boxes)), default=0),
        )

    def draw_multiline(
        self, draw: ImageDraw.ImageDraw, xy: tuple[float, float],
        lines: list[TextLine], fill: tuple[int, int, int], spacing: int,
    ) -> None:
        if all(run.style == TextStyle() for line in lines for run in line.runs):
            self.regular.draw_multiline(draw, xy, [line.text for line in lines], fill, spacing)
            return
        step = self.primary.getbbox("A")[3] + spacing
        for index, line in enumerate(lines):
            x, y = xy[0], xy[1] + index * step
            for run in line.runs:
                font = self._font(run.style)
                width = self._run_width(run)
                color = LINK_COLOR if run.style.link else fill
                if run.style.code:
                    draw.rounded_rectangle(
                        (x, y, x + width, y + sum(self.primary.getmetrics())),
                        radius=3, fill=CODE_BACKGROUND,
                    )
                    cursor = x + 4
                    for glyph in self._code_glyphs(run.text):
                        advance = self.regular.getlength(glyph)
                        cell = max(self._code_cell, advance)
                        self.regular.draw_text(
                            draw, (cursor + (cell - advance) / 2, y), glyph, color,
                        )
                        cursor += cell
                elif run.style.italic:
                    mask = Image.new("RGB", (max(1, math.ceil(width)), sum(self.primary.getmetrics())), (0, 0, 0))
                    font.draw_text(ImageDraw.Draw(mask), (0, 0), run.text, (255, 255, 255))
                    mask = mask.convert("L").transform(
                        mask.size, Image.Transform.AFFINE,
                        (1, 0.2, -0.2 * mask.height, 0, 1, 0),
                        resample=Image.Resampling.BICUBIC,
                    )
                    draw.bitmap((x, y), mask, fill=color)
                else:
                    font.draw_text(draw, (x, y), run.text, color)
                baseline = y + self.primary.getmetrics()[0]
                if run.style.underline:
                    draw.line((x, baseline + 2, x + width, baseline + 2), fill=color, width=1)
                if run.style.strike:
                    strike_y = baseline - self.primary.size * 0.3
                    draw.line((x, strike_y, x + width, strike_y), fill=color, width=1)
                x += width


def wrap_embed_text(text: str, font: EmbedTextFont, width: int) -> list[TextLine]:
    """Wrap styled words, preserving code spacing and explicit line breaks."""
    if width <= 0:
        raise ValueError("width must be positive")
    lines: list[TextLine] = []
    current: list[TextRun] = []

    def flush() -> None:
        if current and not current[-1].style.code:
            last = current.pop()
            _append(current, last.text.rstrip(), last.style)
        lines.append(TextLine(tuple(current)))
        current.clear()

    for run in parse_embed_text(text):
        for token in re.findall(r"\n|[^\S\n]+|[^\s]+", run.text):
            if token == "\n":
                flush()
                continue
            if token.isspace() and not run.style.code:
                if not current:
                    continue
                token = " "
            candidate = list(current)
            _append(candidate, token, run.style)
            if font.getlength(TextLine(tuple(candidate))) <= width:
                current = candidate
                continue
            if current:
                flush()
            if token.isspace() and not run.style.code:
                continue
            for character in token:
                candidate = list(current)
                _append(candidate, character, run.style)
                if current and font.getlength(TextLine(tuple(candidate))) > width:
                    flush()
                _append(current, character, run.style)
    if current:
        flush()
    return lines or [TextLine()]


def truncate_embed_line(line: TextLine, font: EmbedTextFont, width: int) -> TextLine:
    """Keep run styles while reserving measured space for an ellipsis."""
    runs = list(line.runs)
    suffix = TextRun("…")
    while runs and font.getlength(TextLine(tuple(runs) + (suffix,))) > width:
        last = runs.pop()
        _append(runs, last.text[:-1].rstrip(), last.style)
    return TextLine(tuple(runs) + (suffix,))
