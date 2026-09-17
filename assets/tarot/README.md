# Tarot card art

The tarot command composites these bundled Rider-Waite-Smith scans onto the
spread cloth. It does not download card images while handling commands.

The illustrations are the 1909 Pamela Colman Smith / A. E. Waite deck, which is
public domain in the United States (published before 1930). Scans come from
Wikimedia Commons. Bundled WebP files are cropped to the printed frame so cream
paper and scanner white do not show as a thick edge. `sources.json` records each
Commons file name, source URL, and the SHA-256 of the bundled WebP.

File names match card ids in `cogs.funny_things.tarot._tarot_helpers`:

- `back.webp` — original roses-and-lilies card back
- `major-00.webp` … `major-21.webp` — Fool through World
- `wands-01.webp` … `wands-14.webp` — Ace through King, and the same pattern for
  `cups`, `swords`, and `pentacles`

Refresh from the repository root:

```powershell
python scripts/prepare_tarot_assets.py
python scripts/prepare_tarot_assets.py --from-existing
```
