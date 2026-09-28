"""Copy legacy announcement settings to an explicitly chosen guild."""

import argparse
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cogs.settings._guild_variables import (
    ANNOUNCEMENT_VARIABLES,
    ensure_guild_variable_index,
    valid_guild_id,
)


def migrate(collection: Any, guild_id: int, *, apply: bool = False) -> dict[str, int]:
    """Copy missing settings to their supported scope, never deleting source rows."""
    if not valid_guild_id(guild_id):
        raise ValueError("A positive guild ID is required.")
    source_filter = {"guild_id": {"$exists": False}}
    target_filter = {"guild_id": guild_id}
    source = {}
    for row in collection.find(source_filter):
        name = row.get("name")
        if not isinstance(name, str) or name not in ANNOUNCEMENT_VARIABLES:
            continue
        variable_type = row.get("type")
        value = row.get("value")
        if variable_type != "STRING" or not isinstance(value, str) or not value.strip():
            raise ValueError("Announcement settings must contain nonempty STRING values.")
        record = {"guild_id": guild_id, "name": name, "type": "STRING", "value": value}
        if name in source and source[name] != record:
            raise ValueError("Conflicting legacy settings share a name; resolve them first.")
        source[name] = record

    existing_names = {
        row["name"] for row in collection.find(target_filter) if "name" in row
    }
    pending = [record for name, record in source.items() if name not in existing_names]
    inserted = 0
    if apply:
        ensure_guild_variable_index(collection)
        for record in pending:
            result = collection.update_one(
                {**target_filter, "name": record["name"]},
                {"$setOnInsert": record},
                upsert=True,
            )
            inserted += result.upserted_id is not None
    return {
        "source": len(source), "pending": len(pending),
        "preserved": len(source) - len(pending), "inserted": inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guild-id", type=int, required=True,
                        help="Destination Discord guild ID for announcements.")
    parser.add_argument("--apply", action="store_true", help="Write changes; otherwise preview only.")
    args = parser.parse_args()
    if not valid_guild_id(args.guild_id):
        parser.error("--guild-id must be a positive integer")

    from db import db

    try:
        result = migrate(db["global_variables"], args.guild_id, apply=args.apply)
    except ValueError as error:
        parser.error(str(error))
    print(
        f"{'Applied' if args.apply else 'Preview'}: {result['source']} source settings, "
        f"{result['pending']} to copy, {result['preserved']} existing values preserved, "
        f"{result['inserted']} inserted. Source rows are retained."
    )


if __name__ == "__main__":
    main()
