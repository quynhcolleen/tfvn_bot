# Lunch media

The lunch command serves these bundled files as Discord attachments. It does not
download food pictures or GIFs while handling commands.

Food names, estimated prices, vegetarian flags and image sheets come from
[truanayangi](https://github.com/truanayangi-com/truanayangi) at revision
`87c41e2a4eb00834666f2970b278e7ceeaa4886a`. `food_sources.json` records the source
URLs and SHA-256 hashes. These 128 dishes retain their original image IDs; IDs are
not contiguous. Another 24 locally maintained dishes bring the catalog to 152,
including 16 vegetarian options. Local additions live in `data/lunch_foods_extra.json`
and use reserved image IDs starting at 1000. Their prices are editorial estimates
for one serving, not restaurant quotes.

`food-extra-0.webp` and `food-extra-1.webp` contain the matching AI-generated food
illustrations. `extra_sources.json` records the built-in imagegen prompts, image
IDs and file hashes. Each sheet contains twelve dishes in a four-column,
three-row layout. The local sheets use row positions of 0/46/92% and a 6%
bottom-edge trim to avoid clipping plates or including a neighboring dish.
`_lunch_media.py` crops the appropriate picture; the upstream sheets retain
their original row offsets and bottom-edge trims.

Refresh the food catalog and sheets from the repository root:

```powershell
.\venv\Scripts\python.exe scripts/prepare_lunch_assets.py
```

Both an upstream refresh and a local rebuild preserve the extra catalog. After
editing local additions or adding their image sheets, rebuild without network access:

```powershell
.\venv\Scripts\python.exe scripts/prepare_lunch_assets.py --offline
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
