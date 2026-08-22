"""Internal database browser for local inspection and maintenance.

Responsibility
--------------
Expose the registered SQLModel tables as a small, metadata-driven CRUD surface
for the internal dashboard.

Routes
------
``GET /internal/db/tables``                          table inventory and counts
``GET /internal/db/tables/{table}/rows``             paginated table rows
``PATCH /internal/db/tables/{table}/rows/{pk}``      edit one existing row
``DELETE /internal/db/tables/{table}/rows/{pk}``     delete one existing row

There is deliberately no insert route: ids and orchestrator-owned invariants
belong to the pipeline, not a hand-edited browser. These endpoints are
unauthenticated like the rest of this API and are therefore a local/internal
tool. Writes bypass orchestrator invariants and the event bus, so edits do not
live-update other dashboard surfaces.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel
from sqlmodel.sql.sqltypes import AutoString

from app.deps import get_db

router = APIRouter(prefix="/internal/db", tags=["internal"])


class RowPatch(BaseModel):
    """Request body for editing an existing database row."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


def _table_or_404(name: str) -> Table:
    table = SQLModel.metadata.tables.get(name)
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    return table


def _primary_key(table: Table) -> Any | None:
    columns = list(table.primary_key.columns)
    return columns[0] if len(columns) == 1 else None


def _column_descriptor(column: Any) -> dict[str, Any]:
    return {
        "name": column.name,
        "type": str(column.type),
        "nullable": column.nullable,
        "primary_key": column.primary_key,
    }


def _row_count(db: Session, table: Table) -> int:
    return int(db.exec(select(func.count()).select_from(table)).scalar_one())


def _table_descriptor(db: Session, table: Table) -> dict[str, Any]:
    primary_key = _primary_key(table)
    return {
        "name": table.name,
        "primary_key": primary_key.name if primary_key is not None else None,
        "row_count": _row_count(db, table),
        "columns": [_column_descriptor(column) for column in table.columns],
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def _row_dict(row: Any, table: Table) -> dict[str, Any]:
    return {column.name: _json_safe(row._mapping[column]) for column in table.columns}


def _coerce_value(column: Any, value: Any) -> Any:
    if value is None:
        if not column.nullable:
            raise HTTPException(
                status_code=400,
                detail=f"{column.name} cannot be null",
            )
        return None

    if value == "" and column.nullable:
        return None

    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        if isinstance(column.type, AutoString):
            python_type = str
        else:
            raise HTTPException(
                status_code=400,
                detail=f"{column.name} has an unsupported type",
            ) from None

    try:
        if python_type is bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1"}:
                    return True
                if normalized in {"false", "0"}:
                    return False
                raise ValueError
            if isinstance(value, (int, float)) and value in {0, 1}:
                return bool(value)
            raise ValueError
        if python_type is int:
            if isinstance(value, bool):
                raise ValueError
            return int(value)
        if python_type is float:
            return float(value)
        if python_type is datetime:
            if not isinstance(value, str):
                raise ValueError
            return datetime.fromisoformat(value)
        if python_type is str:
            return str(value)
        return python_type(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{column.name} could not be coerced to {python_type.__name__}",
        ) from exc


def _row_by_pk(db: Session, table: Table, primary_key: Any, value: Any) -> Any | None:
    return db.exec(select(table).where(table.c[primary_key.name] == value)).first()


@router.get("/tables")
async def list_tables(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """List every registered table with its columns and current row count."""
    return [
        _table_descriptor(db, table)
        for table in sorted(
            SQLModel.metadata.tables.values(), key=lambda item: item.name
        )
    ]


@router.get("/tables/{table_name}/rows")
async def list_rows(
    table_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a paginated, JSON-safe view of one registered table."""
    table = _table_or_404(table_name)
    order_column = table.c.get("created_at")
    if order_column is None:
        order_column = table.c.get("ts")
    statement = select(table)
    if order_column is not None:
        statement = statement.order_by(order_column.desc())
    else:
        primary_key_columns = list(table.primary_key.columns)
        if primary_key_columns:
            statement = statement.order_by(
                *(column.desc() for column in primary_key_columns)
            )
    rows = db.exec(statement.offset(offset).limit(limit)).all()
    return {
        "columns": [_column_descriptor(column) for column in table.columns],
        "rows": [_row_dict(row, table) for row in rows],
        "total": _row_count(db, table),
    }


@router.patch("/tables/{table_name}/rows/{pk}")
async def update_row(
    table_name: str,
    pk: str,
    payload: RowPatch,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Coerce and update editable columns on one existing row."""
    table = _table_or_404(table_name)
    primary_key = _primary_key(table)
    if primary_key is None:
        raise HTTPException(
            status_code=400, detail="table has no single-column primary key"
        )
    if not payload.values:
        raise HTTPException(status_code=400, detail="values cannot be empty")
    unknown = sorted(set(payload.values) - set(table.columns.keys()))
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown columns: {', '.join(unknown)}"
        )
    if primary_key.name in payload.values:
        raise HTTPException(status_code=400, detail="primary key cannot be changed")

    primary_key_value = _coerce_value(primary_key, pk)
    if _row_by_pk(db, table, primary_key, primary_key_value) is None:
        raise HTTPException(status_code=404, detail="row not found")
    values = {
        name: _coerce_value(table.c[name], value)
        for name, value in payload.values.items()
    }
    try:
        db.exec(
            update(table)
            .where(table.c[primary_key.name] == primary_key_value)
            .values(**values)
        )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="update violates a database constraint"
        ) from exc

    updated = _row_by_pk(db, table, primary_key, primary_key_value)
    if updated is None:
        raise HTTPException(status_code=404, detail="row not found")
    return _row_dict(updated, table)


@router.delete("/tables/{table_name}/rows/{pk}", status_code=204)
async def delete_row(
    table_name: str,
    pk: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete one row, preserving SQLite's foreign-key failure semantics."""
    table = _table_or_404(table_name)
    primary_key = _primary_key(table)
    if primary_key is None:
        raise HTTPException(
            status_code=400, detail="table has no single-column primary key"
        )

    primary_key_value = _coerce_value(primary_key, pk)
    if _row_by_pk(db, table, primary_key, primary_key_value) is None:
        raise HTTPException(status_code=404, detail="row not found")
    try:
        db.exec(delete(table).where(table.c[primary_key.name] == primary_key_value))
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="delete violates a database constraint"
        ) from exc
    return Response(status_code=204)
