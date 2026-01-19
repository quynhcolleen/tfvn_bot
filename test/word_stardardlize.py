def normalize_old_tone(s: str) -> str:
    """
    Convert legacy tone placement → modern standard
    (mainly oa/oe/ua/ia/ya/ưa groups)
    """
    replacements = {
        # oa group
        'oà': 'òa', 'oá': 'óa', 'oả': 'ỏa', 'oã': 'õa', 'oạ': 'ọa',
        'òa': 'òa', 'óa': 'óa', 'ỏa': 'ỏa', 'õa': 'õa', 'ọa': 'ọa',  # already correct

        # oe group (rare)
        'oè': 'òe', 'oé': 'óe', 'oẻ': 'ỏe', 'oẽ': 'õe', 'oẹ': 'ọe',

        # ua group
        'uà': 'ùa', 'uá': 'úa', 'uả': 'ủa', 'uã': 'ũa', 'uạ': 'ụa',

        # ưa group
        'ưà': 'ừa', 'ưá': 'ứa', 'ưả': 'ửa', 'ưã': 'ữa', 'ưạ': 'ựa',

        # ia / ya group
        'ià': 'ìa', 'iá': 'ía', 'iả': 'ỉa', 'iã': 'ĩa', 'ịa': 'ịa',
        'yà': 'ỳa', 'yá': 'ýa', 'yả': 'ỷa', 'yã': 'ỹa', 'yạ': 'ỵa',

        # uy group (very common in your data)
        'uỳ': 'ủy', 'uý': 'úy', 'uỷ': 'ủy', 'uỹ': 'ũy', 'ụy': 'ụy',

        # uô / ô group (less frequent misplacement)
        'uồ': 'uồ', 'uố': 'uố', 'uổ': 'uổ', 'uỗ': 'uỗ', 'uộ': 'uộ',
    }

    for old, new in replacements.items():
        s = s.replace(old, new)

    # Optional: fix some very common mistakes seen in old texts
    s = s.replace('quì', 'quỳ').replace('quỵ', 'quỵ')   # quỳ is usually kept

    return s.strip()


# === Apply to your file ===
with open('./data/word_connect_valid_list.txt', encoding='utf-8') as f:
    lines = f.readlines()

normalized_lines = [normalize_old_tone(line) for line in lines]

# Save result
with open('./data/word_connect_valid_list_normalized.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(normalized_lines).rstrip() + '\n')

normalized_lines = [normalize_old_tone(line) for line in lines]

# Remove duplicates, keep first occurrence
seen = set()
unique_lines = []
for line in normalized_lines:
    stripped = line.strip()
    if stripped and stripped not in seen:
        seen.add(stripped)
        unique_lines.append(line)

# Save
with open('./data/word_connect_valid_list_normalized_unique.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(unique_lines).rstrip() + '\n')

print(f"Normalized {len(lines)} lines, {len(unique_lines)} unique lines saved.")