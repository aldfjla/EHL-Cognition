#!/usr/bin/env python3
"""Report (and optionally repair) drift between robotci.db and the SQLModel tables.

Why this exists: schema evolution is ``create_all`` only, which creates missing
tables and never alters an existing one (see ``apps/api/app/store/db.py``). A
database created before a column was added keeps working until something selects
that column, and then one endpoint 500s with ``no such column`` while every other
page looks fine. This turns that landmine into one command.

    python scripts/schema_drift.py           # report only, exit 1 on drift
    python scripts/schema_drift.py --apply   # add the missing columns

``--apply`` only ever adds columns. Dropped or retyped columns are reported and
left alone: SQLite cannot do either without a table rebuild, and a rebuild is a
data-loss risk this script has no business taking silently.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "orchestrator"))

# Imported after the sys.path bootstrap above so the script runs uninstalled.
from app.config import get_settings
from app.store import tables as _tables  # noqa: F401  (registers the models)
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlmodel import SQLModel

_DIALECT = sqlite_dialect.dialect()


def parse_args() -> argparse.Namespace:
    """CLI: --db, --apply."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite file to inspect (default: DATABASE_URL from .env).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Add the missing columns instead of only reporting them.",
    )
    return parser.parse_args()


def resolve_db_path(explicit: str | None) -> Path:
    """The SQLite file to inspect, or exit with an actionable message."""
    if explicit:
        return Path(explicit)
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        print(f"{url} is not SQLite; this script only inspects SQLite files.")
        raise SystemExit(2)
    return Path(url.split("///", 1)[-1])


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str] | None:
    """Column names for ``table``, or ``None`` when the table is absent."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows} if rows else None


def add_column_sql(table: str, column) -> str | None:
    """``ALTER TABLE`` for one missing column, or ``None`` if it needs a rebuild.

    A NOT NULL column can only be added when it carries a default: existing rows
    have to be given some value, and inventing one is the caller's decision.
    """
    type_sql = column.type.compile(dialect=_DIALECT)
    clause = f"ALTER TABLE {table} ADD COLUMN {column.name} {type_sql}"
    if column.nullable:
        return clause
    default = getattr(column.default, "arg", None) if column.default else None
    if default is None:
        return None
    literal = f"'{default}'" if isinstance(default, str) else repr(default)
    return f"{clause} NOT NULL DEFAULT {literal}"


def main() -> int:
    """Report drift per table; with --apply, add what can be added."""
    args = parse_args()
    path = resolve_db_path(args.db)
    if not path.exists():
        print(f"{path} does not exist — the API creates it on first start.")
        return 2

    conn = sqlite3.connect(path)
    missing_tables: list[str] = []
    statements: list[str] = []
    needs_rebuild: list[str] = []

    for name, table in sorted(SQLModel.metadata.tables.items()):
        present = existing_columns(conn, name)
        if present is None:
            missing_tables.append(name)
            continue
        for column in table.columns:
            if column.name in present:
                continue
            sql = add_column_sql(name, column)
            if sql is None:
                needs_rebuild.append(f"{name}.{column.name} (NOT NULL, no default)")
            else:
                statements.append(sql)

    if not statements and not missing_tables and not needs_rebuild:
        print(f"{path}: schema matches the models.")
        return 0

    print(f"{path}: drift detected.\n")
    if missing_tables:
        print("Missing tables (the API creates these at startup):")
        for name in missing_tables:
            print(f"  {name}")
        print()
    if statements:
        print("Missing columns:")
        for sql in statements:
            print(f"  {sql}")
        print()
    if needs_rebuild:
        print("Cannot be added in place — recreate the database instead:")
        for item in needs_rebuild:
            print(f"  {item}")
        print()

    if not statements:
        return 1
    if not args.apply:
        print("Re-run with --apply to add the missing columns.")
        return 1

    for sql in statements:
        conn.execute(sql)
    conn.commit()
    print(f"Applied {len(statements)} column(s). Restart the API to pick them up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
