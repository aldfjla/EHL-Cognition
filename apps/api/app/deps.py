"""Dependency injection: the store, the event bus, and the Devin client.

Responsibility
--------------
Give routers their collaborators as FastAPI dependencies so tests can swap in
fakes without patching module globals.

Inputs:  :class:`~app.config.Settings`.
Outputs: process-lifetime singletons (bus, Devin client) and per-request
         resources (a database session).

Lifetime rules
--------------
* Bus and Devin client are created once at app startup and shared — the bus
  must be shared or a WebSocket subscribes to a different bus than the pipeline
  publishes to, which is the single easiest way to build a dashboard that
  renders nothing.
* Database sessions are per-request and always closed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from app.config import Settings, get_settings


def get_config() -> Settings:
    """FastAPI dependency wrapping :func:`~app.config.get_settings`."""
    return get_settings()


def get_db() -> Iterator[Any]:
    """Yield a database session for one request, closing it afterwards."""
    raise NotImplementedError
    # TODO(build): yield from app.store.db.session_scope().


def get_bus() -> Any:
    """The process-wide :class:`~orchestrator.bus.EventBus`."""
    raise NotImplementedError
    # TODO(build): return the singleton created in main.py's lifespan.


def get_devin() -> Any:
    """The shared :class:`~orchestrator.devin.client.DevinClient`."""
    raise NotImplementedError
    # TODO(build): return the singleton; raise a clear error if the API key
    # is unset rather than failing at the first session create.


async def get_run_or_404(run_id: str, db: Any = None) -> Any:
    """Fetch a run by id or raise ``HTTPException(404)``."""
    raise NotImplementedError
    # TODO(build): repo.get_run, raise HTTPException(404, "run not found").


async def lifespan(app: Any) -> AsyncIterator[None]:
    """Startup/shutdown: create singletons, init the DB, close cleanly."""
    raise NotImplementedError
    # TODO(build): init_db(), construct EventBus + DevinClient, yield,
    # then await devin.close().
