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


def get_engine() -> Any:
    """Create (once) and return the SQLModel engine."""
    raise NotImplementedError
    # TODO(build): create_engine(settings.database_url, connect_args={
    # "check_same_thread": False}); PRAGMA journal_mode=WAL and busy_timeout
    # via a connect event listener.


def init_db() -> None:
    """Create tables if absent. Called from the app lifespan."""
    raise NotImplementedError
    # TODO(build): SQLModel.metadata.create_all(get_engine()).


@contextmanager
def session_scope() -> Iterator[Any]:
    """Transactional session: commit on success, rollback on error, always close."""
    raise NotImplementedError
    # TODO(build): Session(engine), try/yield/commit, except rollback+raise,
    # finally close.


# TODO(build): decide on migrations. create_all is fine while the schema is
# churning; the moment a demo DB needs to survive a schema change, add alembic.
