"""Highlight text fallbacks for meter blocks and composite emoji."""

from __future__ import annotations

import math
import re
from threading import local

from PIL import ImageDraw, ImageFont

from cogs.utils._quote_card import _FallbackFont, _load_fallback_font


_DRAWN_GLYPHS = re.compile(r"(🏳(?:\ufe0f)?\u200d🌈|[█░▒▓]+)")
_FONT_CACHE = local()


class _DrawnGlyphFont:
    """Measure the exact geometry used for glyphs missing from bundled fonts."""

    def __init__(self, primary: ImageFont.FreeTypeFont, *, flag: bool) -> None:
        self.size = primary.size
        self.flag = flag
        self.advance = self.size * 1.25 if flag else primary.getlength("0")
        self.top = -math.ceil(self.size * 0.9)
        self.bottom = self.top + self.size

    def getlength(self, text: str) -> float:
        return self.advance * (1 if self.flag and text else len(text))

    def getbbox(self, text: str, *, anchor: str = "ls") -> tuple[int, int, int, int]:
        if anchor != "ls":
            raise ValueError("Drawn highlight glyphs require a baseline anchor.")
        return 0, self.top, math.ceil(self.getlength(text)), self.bottom

    def draw(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: str,
        fill: tuple[int, int, int],
    ) -> None:
        x, baseline = xy
        top = round(baseline + self.top)
        bottom = round(baseline + self.bottom) - 1
        if self.flag:
            # Basic-layout Pillow cannot shape ZWJ sequences. Draw this flag as
            # one symbol on every platform, including Docker without RAQM.
            left = round(x) + 2
            right = round(x + self.advance) - 3
            pole_width = max(1, self.size // 12)
            draw.rectangle((left, top, left + pole_width - 1, bottom), fill=fill)
            colors = (
                (229, 57, 53), (251, 140, 0), (253, 216, 53),
                (67, 160, 71), (30, 136, 229), (142, 36, 170),
            )
            flag_height = max(6, round(self.size * 0.75))
            for index, color in enumerate(colors):
                stripe_top = top + round(index * flag_height / len(colors))
                stripe_bottom = top + round((index + 1) * flag_height / len(colors)) - 1
                draw.rectangle(
                    (left + pole_width, stripe_top, right, stripe_bottom), fill=color,
                )
            return

        for index, character in enumerate(text):
            left = round(x + index * self.advance)
            right = round(x + (index + 1) * self.advance) - 1
            if character == "█":
                draw.rectangle((left, top, right, bottom), fill=fill)
                continue
            density = {"░": 1, "▒": 2, "▓": 3}[character]
            for pixel_y in range(top, bottom + 1):
                for pixel_x in range(left, right + 1):
                    # A stable 2x2 dither gives 25%, 50%, or 75% coverage.
                    phase = ((pixel_x - left) % 2 + 2 * ((pixel_y - top) % 2))
                    if phase < density:
                        draw.point((pixel_x, pixel_y), fill=fill)


class _HighlightFont(_FallbackFont):
    """Keep normal font fallback while supplying portable meter/flag glyphs."""

    def __init__(self, font: _FallbackFont) -> None:
        super().__init__(font.primary, font.fonts[1:])
        self._blocks = _DrawnGlyphFont(self.primary, flag=False)
        self._flag = _DrawnGlyphFont(self.primary, flag=True)

    def _font_runs(
        self, text: str,
    ) -> list[tuple[str, ImageFont.FreeTypeFont | _DrawnGlyphFont]]:
        runs: list[tuple[str, ImageFont.FreeTypeFont | _DrawnGlyphFont]] = []
        for part in _DRAWN_GLYPHS.split(text):
            if not part:
                continue
            if part[0] in "█░▒▓":
                runs.append((part, self._blocks))
            elif _DRAWN_GLYPHS.fullmatch(part):
                runs.append((part, self._flag))
            else:
                runs.extend(super()._font_runs(part))
        return runs

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
            if isinstance(font, _DrawnGlyphFont):
                font.draw(draw, (x, baseline), run, fill)
            else:
                draw.text((x, baseline), run, font=font, fill=fill, anchor="ls")
            x += font.getlength(run)


def load_highlight_font(size: int, *, bold: bool = False) -> _HighlightFont:
    """Load a thread-local highlight font without changing quote rendering."""
    cache = getattr(_FONT_CACHE, "fonts", None)
    if cache is None:
        cache = {}
        _FONT_CACHE.fonts = cache
    key = (size, bold)
    if key not in cache:
        cache[key] = _HighlightFont(_load_fallback_font(size, bold=bold))
    return cache[key]
