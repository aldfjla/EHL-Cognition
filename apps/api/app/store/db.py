"""SQLite engine and session management.

Responsibility
--------------
Create the engine, initialise the schema, and hand out sessions.

Inputs:  ``DATABASE_URL`` from settings.
Outputs: a module-level engine and a ``session_scope`` context manager.

SQLite specifics that actually bite
-----------------------------------
* ``check_same_thread=False`` is required — FastAPI runs handlers on a thread
  pool and the pipeline runs on the event loop.
* WAL mode, so the pipeline can write while the dashboard reads. Without it a
  suite run blocks every dashboard poll and the UI looks hung.
* ``busy_timeout``, because parallel scenario workers writing results will
  otherwise raise "database is locked" under exactly the load the demo creates.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings
from app.store import tables  # noqa: F401  (registers the tables on SQLModel.metadata)
from app.store.migrate import repair_schema

#: How long a writer waits on a locked database before giving up.
BUSY_TIMEOUT_MS = 5000

_engine: Engine | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Put every SQLite connection into WAL mode with a busy timeout."""
    if type(dbapi_connection).__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_engine() -> Engine:
    """Create (once) and return the SQLModel engine."""
    global _engine
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if _is_sqlite(url) else {}
        _engine = create_engine(url, connect_args=connect_args, echo=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine. Testing hook — production never calls this."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db() -> None:
    """Create tables if absent. Called from the app lifespan."""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    if not _is_sqlite(get_settings().database_url):
        return
    connection = engine.raw_connection()
    try:
        repair_schema(connection, apply=True)
    finally:
        connection.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error, always close."""
    session = Session(get_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
