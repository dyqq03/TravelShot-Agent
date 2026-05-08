from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import seed JSONL spots into PostgreSQL photo_spots.")
    parser.add_argument("--database-url", help="Override DATABASE_URL for this import.")
    parser.add_argument("--spot-data-dir", help="Override SPOT_DATA_DIR. Defaults to db/seed/spots.")
    return parser.parse_args()


async def _run() -> None:
    sys.path.insert(0, str(API_ROOT))

    from app.db.postgres import close_postgres, connect_postgres, init_schema
    from app.db.repository import upsert_photo_spots
    from app.spot.repository import load_spots

    pool = await connect_postgres()
    try:
        await init_schema(pool)
        spots = list(load_spots())
        count = await upsert_photo_spots(pool, spots)
        print(f"Imported {count} spots into photo_spots.")
    finally:
        await close_postgres()


def main() -> None:
    args = _parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    if args.spot_data_dir:
        os.environ["SPOT_DATA_DIR"] = args.spot_data_dir
    else:
        os.environ.setdefault("SPOT_DATA_DIR", str(ROOT / "db" / "seed" / "spots"))
    asyncio.run(_run())


if __name__ == "__main__":
    main()
