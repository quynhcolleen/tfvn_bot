import argparse
import sys
from pathlib import Path

from pymongo import UpdateOne

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from assets.nsfw_gifs import (
    ASSJOB_GIFS,
    BLOWJOB_GIFS,
    CREAMPIE_GIFS,
    FACESIT_GIFS,
    FINGERING_GIFS,
    FOOTJOB_GIFS,
    FROTTING_GIFS,
    FUCKING_GIFS,
    GANGBANG_GIFS,
    HANDJOB_GIFS,
    RIDE_GIFS,
    RIMJOB_GIFS,
    SPANK_GIFS,
    THIGHJOB_GIFS,
    THREESOME_GIFS,
)


NSFW_GIF_VARIABLES = {
    "BLOWJOB_GIFS": BLOWJOB_GIFS,
    "HANDJOB_GIFS": HANDJOB_GIFS,
    "FOOTJOB_GIFS": FOOTJOB_GIFS,
    "ASSJOB_GIFS": ASSJOB_GIFS,
    "THIGHJOB_GIFS": THIGHJOB_GIFS,
    "SPANK_GIFS": SPANK_GIFS,
    "RIMJOB_GIFS": RIMJOB_GIFS,
    "FROTTING_GIFS": FROTTING_GIFS,
    "FUCKING_GIFS": FUCKING_GIFS,
    "CREAMPIE_GIFS": CREAMPIE_GIFS,
    "THREESOME_GIFS": THREESOME_GIFS,
    "GANGBANG_GIFS": GANGBANG_GIFS,
    "FINGERING_GIFS": FINGERING_GIFS,
    "RIDE_GIFS": RIDE_GIFS,
    "FACESIT_GIFS": FACESIT_GIFS,
}


def migrate(overwrite: bool = False) -> None:
    from db import db

    collection = db["global_variables"]
    update_operator = "$set" if overwrite else "$setOnInsert"
    operations = [
        UpdateOne(
            {"name": name},
            {
                update_operator: {
                    "name": name,
                    "type": "ARRAY",
                    "value": gifs,
                }
            },
            upsert=True,
        )
        for name, gifs in NSFW_GIF_VARIABLES.items()
    ]
    result = collection.bulk_write(operations)
    print(
        f"Matched {result.matched_count}, modified {result.modified_count}, "
        f"inserted {result.upserted_count} NSFW GIF variables."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate the hardcoded NSFW GIF arrays to global_variables."
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace values that already exist in the database.",
    )
    args = parser.parse_args()
    migrate(overwrite=args.overwrite)
