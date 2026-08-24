from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import JSON, func, insert, select
from sqlalchemy.engine import Engine

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.json_payload import compact_json_payload  # noqa: E402
from app.db.session import create_database_engine, initialize_database  # noqa: E402
import app.models  # noqa: E402,F401


def _table_counts(database_engine: Engine) -> dict[str, int]:
    with database_engine.connect() as connection:
        return {
            table.name: int(connection.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


def copy_database(source_url: str, target_url: str, batch_size: int = 200) -> dict[str, int]:
    if source_url == target_url:
        raise ValueError("Source and target database URLs must be different")

    source_engine = create_database_engine(source_url)
    target_engine = create_database_engine(target_url)
    initialize_database(target_engine)

    try:
        existing_counts = _table_counts(target_engine)
        nonempty_tables = {name: count for name, count in existing_counts.items() if count}
        if nonempty_tables:
            detail = ", ".join(f"{name}={count}" for name, count in nonempty_tables.items())
            raise RuntimeError(
                "Local target already contains data. Refusing to merge or overwrite: " + detail
            )

        copied_counts: dict[str, int] = {}
        with source_engine.connect() as source, target_engine.begin() as target:
            for table in Base.metadata.sorted_tables:
                result = source.execute(select(table))
                copied = 0
                while rows := result.mappings().fetchmany(batch_size):
                    target.execute(insert(table), [_compact_row(table, row) for row in rows])
                    copied += len(rows)
                copied_counts[table.name] = copied

        target_counts = _table_counts(target_engine)
        mismatches = {
            name: (copied_counts[name], target_counts.get(name, 0))
            for name in copied_counts
            if copied_counts[name] != target_counts.get(name, 0)
        }
        if mismatches:
            raise RuntimeError(f"Copy verification failed: {mismatches}")
        return copied_counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _compact_row(table: object, row: object) -> dict[str, object]:
    payload = dict(row)
    for column in table.columns:
        if isinstance(column.type, JSON) and column.name in payload:
            payload[column.name] = compact_json_payload(payload[column.name])
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy the configured external database into the project-local SQLite file."
    )
    parser.add_argument(
        "--source-url",
        default=(settings.database_url or "").strip(),
        help="Source SQLAlchemy URL. Defaults to DATABASE_URL from apps/api/.env.",
    )
    parser.add_argument(
        "--target-url",
        default=settings.local_database_url,
        help="Target SQLite URL. Defaults to LOCAL_DATA_DIR/LOCAL_DATABASE_FILENAME.",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    if not args.source_url:
        parser.error("A source URL is required via DATABASE_URL or --source-url")
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    counts = copy_database(args.source_url, args.target_url, args.batch_size)
    total = sum(counts.values())
    print(f"Copied {total} rows across {len(counts)} tables")
    for table_name, count in counts.items():
        print(f"{table_name}: {count}")
    print(f"Local target: {args.target_url}")


if __name__ == "__main__":
    main()
