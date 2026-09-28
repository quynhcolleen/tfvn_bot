"""Guild-scoped announcement settings; other features use shared global_vars."""

from typing import Any


ANNOUNCEMENT_VARIABLES = frozenset({
    "JOIN_CHANNEL", "RULE_CHANNEL", "ROLE_CHANNEL", "BYE_CHANNEL",
    "WELCOME_GIF_URL", "GOODBYE_GIF_URL", "BANNED_GIF_URL",
})


def valid_guild_id(guild_id: object) -> bool:
    return isinstance(guild_id, int) and not isinstance(guild_id, bool) and guild_id > 0


def get_guild_variables(bot: object, guild_id: int) -> dict[str, str]:
    """Read this guild's announcement settings without shared-value fallback."""
    if not valid_guild_id(guild_id):
        return {}
    variables = getattr(bot, "guild_vars", None)
    if not isinstance(variables, dict):
        return {}
    guild_variables = variables.get(guild_id)
    if not isinstance(guild_variables, dict):
        return {}
    return {name: value for name, value in guild_variables.items()
            if name in ANNOUNCEMENT_VARIABLES}


def get_guild_variable(
    bot: object, guild_id: int, name: str, default: Any = None
) -> Any:
    return get_guild_variables(bot, guild_id).get(name, default)


def ensure_guild_variable_index(collection: Any) -> None:
    """Keep new settings unique while leaving unassigned legacy rows intact."""
    collection.create_index(
        [("guild_id", 1), ("name", 1)],
        name="guild_variable_name_unique",
        unique=True,
        partialFilterExpression={"guild_id": {"$type": "number"}},
    )
