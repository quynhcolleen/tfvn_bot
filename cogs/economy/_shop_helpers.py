"""Catalog ID, price, display, and listing helpers for the Trap Coin shop."""

from __future__ import annotations

import re
from typing import Any


ITEM_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ITEM_TYPE_BADGE = "badge"
ITEM_TYPE_ROLE = "role"
ITEM_TYPE_CUSTOM_ROLE = "custom_role"
VALID_ITEM_TYPES = frozenset(
    {ITEM_TYPE_BADGE, ITEM_TYPE_ROLE, ITEM_TYPE_CUSTOM_ROLE}
)
CUSTOM_ROLE_ITEM_ID = "custom_role"
RESERVED_ITEM_IDS = frozenset({CUSTOM_ROLE_ITEM_ID})
ITEM_TYPE_ICONS = {
    ITEM_TYPE_ROLE: "🎭",
    ITEM_TYPE_BADGE: "🏷️",
    ITEM_TYPE_CUSTOM_ROLE: "🎨",
}
ITEM_TYPE_LABELS = {
    ITEM_TYPE_ROLE: "Role",
    ITEM_TYPE_BADGE: "Badge",
    ITEM_TYPE_CUSTOM_ROLE: "Custom role",
}
MAX_CATALOG_ITEMS = 25
SELECT_LABEL_LIMIT = 100
SELECT_DESCRIPTION_LIMIT = 100


def normalize_item_id(value: str) -> str:
    """Normalize and validate a stable shop item identifier."""
    item_id = value.strip().lower()
    if not ITEM_ID_PATTERN.fullmatch(item_id):
        raise ValueError(
            "Item ID must contain 1-32 lowercase letters, numbers, "
            "underscores, or hyphens."
        )
    return item_id


def validate_price(value: int) -> int:
    """Return a valid positive shop price."""
    if value <= 0 or value > 1_000_000_000:
        raise ValueError("Price must be between 1 and 1,000,000,000.")
    return value


def clean_display_text(value: str, *, fallback: str, limit: int) -> str:
    """Collapse whitespace in user-facing catalog text and enforce a limit."""
    cleaned = " ".join(value.split()) or fallback
    return cleaned[:limit]


def format_price(value: int) -> str:
    return f"{value:,} TC"


def item_icon(item_type: str) -> str:
    return ITEM_TYPE_ICONS.get(item_type, "🛍️")


def item_type_label(item_type: str) -> str:
    return ITEM_TYPE_LABELS.get(item_type, item_type)


def is_reserved_item_id(item_id: str) -> bool:
    return item_id in RESERVED_ITEM_IDS


def catalog_option_description(item: dict[str, Any]) -> str:
    """Build a select-menu description for one catalog or inventory item."""
    price = format_price(int(item.get("price", 0)))
    label = item_type_label(str(item.get("item_type", "")))
    return f"{price} · {label}"[:SELECT_DESCRIPTION_LIMIT]


def catalog_option_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or item.get("item_id") or "Vật phẩm")
    return name[:SELECT_LABEL_LIMIT]
