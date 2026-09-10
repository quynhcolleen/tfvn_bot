# Lunch media

The lunch command serves these bundled files as Discord attachments. It does not
download food pictures or GIFs while handling commands.

Food names, estimated prices, vegetarian flags and image sheets come from
[truanayangi](https://github.com/truanayangi-com/truanayangi) at revision
`87c41e2a4eb00834666f2970b278e7ceeaa4886a`. `food_sources.json` records the source
URLs and SHA-256 hashes. The 128 dishes retain their original image IDs; IDs are
not contiguous. `_lunch_media.py` follows the upstream `FoodImage` sheet mapping,
row offsets and bottom-edge trims when producing individual PNG attachments.

Refresh the food catalog and sheets from the repository root:

```powershell
.\venv\Scripts\python.exe scripts/prepare_lunch_assets.py
```

To import a newer snapshot, pass `--revision <full-commit-sha>`, review upstream
`FoodImage` for mapping changes, and run the lunch tests before committing.

The blue, purple and gold Genshin Impact wish animations are sourced from the
Tenor pages recorded in `wish_sources.json`. That file records each GIF's direct
URL, source page, hash and measured playback duration. Maintain these three GIFs
separately from the food preparation script. The result replaces the GIF after
its recorded duration: 3 seconds for blue, 6 for purple and 6.08 for gold.

Animation colors are cosmetic price bands: up to 65k VND is blue, over 65k through
130k is purple, and over 130k is gold. The random selection gives every matching
dish equal probability; rerolls exclude the current dish when alternatives exist.
