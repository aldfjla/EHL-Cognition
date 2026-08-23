"""Small, additive SQLite schema repair for metadata drift."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlmodel import SQLModel

log = logging.getLogger("robotci.store.migrate")
_DIALECT = sqlite_dialect.dialect()


@dataclass(frozen=True)
class SchemaDrift:
    """The additive changes and rebuild-only changes found in a database."""

    missing_tables: list[str]
    statements: list[str]
    needs_rebuild: list[str]

    @property
    def has_drift(self) -> bool:
        """Whether the database differs from the current metadata."""
        return bool(self.missing_tables or self.statements or self.needs_rebuild)


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str] | None:
    """Column names for ``table``, or ``None`` when the table is absent."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows} if rows else None


def add_column_sql(table: str, column: Any) -> str | None:
    """Build an additive ALTER statement, or None when a rebuild is required."""
    type_sql = column.type.compile(dialect=_DIALECT)
    clause = f"ALTER TABLE {table} ADD COLUMN {column.name} {type_sql}"
    if column.nullable:
        return clause
    default = getattr(column.default, "arg", None) if column.default else None
    if default is None:
        return None
    literal = f"'{default}'" if isinstance(default, str) else repr(default)
    return f"{clause} NOT NULL DEFAULT {literal}"


def inspect_schema(conn: sqlite3.Connection) -> SchemaDrift:
    """Compare an SQLite connection with the registered SQLModel metadata."""
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

    return SchemaDrift(missing_tables, statements, needs_rebuild)


def apply_schema_repair(
    conn: sqlite3.Connection,
    drift: SchemaDrift,
    *,
    logger: logging.Logger = log,
) -> int:
    """Apply safe ALTER statements and warn about changes needing a rebuild."""
    for statement in drift.statements:
        logger.info("Applying additive schema repair: %s", statement)
        conn.execute(statement)
    if drift.statements:
        conn.commit()
    if drift.needs_rebuild:
        logger.warning(
            "Schema columns require a table rebuild: %s",
            ", ".join(drift.needs_rebuild),
        )
    return len(drift.statements)


def repair_schema(
    conn: sqlite3.Connection,
    *,
    apply: bool = False,
    logger: logging.Logger = log,
) -> SchemaDrift:
    """Inspect a database and optionally apply its safe additive repairs."""
    drift = inspect_schema(conn)
    if apply:
        apply_schema_repair(conn, drift, logger=logger)
    return drift
