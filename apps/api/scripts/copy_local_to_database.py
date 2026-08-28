from __future__ import annotations

import argparse
from pathlib import Path
import sys


API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.core.config import settings  # noqa: E402
from scripts.copy_database_to_local import copy_database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy the project-local SQLite database into an empty external database schema."
    )
    parser.add_argument("--source-url", default=settings.local_database_url)
    parser.add_argument("--target-url", default=(settings.database_url or "").strip())
    parser.add_argument("--target-schema", default=settings.database_schema)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    if not args.target_url:
        parser.error("A target URL is required via DATABASE_URL or --target-url")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    counts = copy_database(
        args.source_url,
        args.target_url,
        args.batch_size,
        target_schema=args.target_schema,
    )
    print(f"Copied {sum(counts.values())} rows across {len(counts)} tables")
    for table_name, count in counts.items():
        print(f"{table_name}: {count}")
    print("Target database copy verified")


if __name__ == "__main__":
    main()
