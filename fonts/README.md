# Bundled fonts

The quote-card renderer loads these fonts directly from this directory so
local Python runs and Docker deployments render the same glyphs without
depending on system font packages. They were downloaded from Google Fonts on
2026-08-01.

| Bundled file | Google Fonts source | SHA-256 | License |
| --- | --- | --- | --- |
| `NotoSans-Variable.ttf` | [`NotoSans[wdth,wght].ttf`](https://github.com/google/fonts/tree/main/ofl/notosans) | `BFB7BB691513F12E734DC346C03A03F784912432D7E3FA8E56EFCF906FE86B3D` | [OFL.txt](OFL.txt) |
| `NotoEmoji-Variable.ttf` | [`NotoEmoji[wght].ttf`](https://github.com/google/fonts/tree/main/ofl/notoemoji) | `DE6C18832938AFC99CAF132B39D6A30A19BAC7F2E812E28DB2535B4608D27551` | [NotoEmoji-OFL.txt](NotoEmoji-OFL.txt) |
| `NotoSansSymbols-Variable.ttf` | [`NotoSansSymbols[wght].ttf`](https://github.com/google/fonts/tree/main/ofl/notosanssymbols) | `F7E7E04B4A24B6C78893D50CBFD2B2F6CAE49617AB047BFEF668D252ADB128F7` | [NotoSansSymbols-OFL.txt](NotoSansSymbols-OFL.txt) |
| `NotoSansSymbols2-Regular.ttf` | [`NotoSansSymbols2-Regular.ttf`](https://github.com/google/fonts/tree/main/ofl/notosanssymbols2) | `7D5FB73B7CA67A6798101741F5D280A3D016A56A197AFCD4199DBB57B4B82A21` | [NotoSansSymbols2-OFL.txt](NotoSansSymbols2-OFL.txt) |

All four fonts use the SIL Open Font License 1.1. Noto Sans is the primary
Vietnamese-capable text font. Quote cards and highlight chat mockups share this
same bundled set. The other three are offline fallbacks for emoji,
music marks, dingbats, and decorative symbols that would otherwise render as
blank rectangles. Quote cards draw supported single-glyph emoji with Noto
Emoji and convert complex or newer unsupported sequences to readable
`:shortcode:` text so output stays consistent without a system shaping engine.
Highlight cards preserve Unicode emoji and draw meter blocks (`█`, `░`) and the
rainbow flag directly, including on systems without a text shaping engine.
