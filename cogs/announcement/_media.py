"""Resolve optional announcement images without sharing guild overrides."""

from urllib.parse import urlsplit

from cogs.settings._guild_variables import get_guild_variable


def announcement_gif_url(
    bot: object, guild_id: int, name: str, default: str
) -> str:
    """Use an HTTP(S) guild override, falling back to the bundled image."""
    value = get_guild_variable(bot, guild_id, name)
    if not isinstance(value, str):
        return default
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        return default
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return default
        # Accessing port also rejects malformed port numbers.
        parsed.port
    except ValueError:
        return default
    return value
