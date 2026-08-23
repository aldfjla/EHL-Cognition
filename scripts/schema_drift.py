#!/usr/bin/env python3
"""Report (and optionally repair) drift between robotci.db and the SQLModel tables.

Why this exists: the API self-heals safe additive drift at startup, but this
command makes the same inspection and repair available before starting it. A
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
from app.store import migrate
from app.store import tables as _tables  # noqa: F401  (registers the models)


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


def main() -> int:
    """Report drift per table; with --apply, add what can be added."""
    args = parse_args()
    path = resolve_db_path(args.db)
    if not path.exists():
        print(f"{path} does not exist — the API creates it on first start.")
        return 2

    conn = sqlite3.connect(path)
    drift = migrate.repair_schema(conn, apply=False)

    if not drift.has_drift:
        print(f"{path}: schema matches the models.")
        return 0

    print(f"{path}: drift detected.\n")
    if drift.missing_tables:
        print("Missing tables (the API creates these at startup):")
        for name in drift.missing_tables:
            print(f"  {name}")
        print()
    if drift.statements:
        print("Missing columns:")
        for sql in drift.statements:
            print(f"  {sql}")
        print()
    if drift.needs_rebuild:
        print("Cannot be added in place — recreate the database instead:")
        for item in drift.needs_rebuild:
            print(f"  {item}")
        print()

    if not drift.statements:
        return 1
    if not args.apply:
        print("Re-run with --apply to add the missing columns.")
        return 1

    applied = migrate.apply_schema_repair(conn, drift)
    print(f"Applied {applied} column(s). Restart the API to pick them up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
