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

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

from fastapi import Depends, HTTPException
from orchestrator.bus import EventBus
from orchestrator.devin.client import DevinClient
from orchestrator.schemas import Run
from sqlmodel import Session

from app.config import Settings, get_settings, validate_paths
from app.store import repo
from app.store.db import init_db, session_scope

log = logging.getLogger("robotci.api")

_bus: EventBus | None = None
_devin: DevinClient | None = None


def get_config() -> Settings:
    """FastAPI dependency wrapping :func:`~app.config.get_settings`."""
    return get_settings()


def get_db() -> Iterator[Session]:
    """Yield a database session for one request, closing it afterwards."""
    with session_scope() as session:
        yield session


def get_bus() -> EventBus:
    """The process-wide :class:`~orchestrator.bus.EventBus`.

    Created lazily so development clients and the tests get the same bus the
    app uses without having to run the lifespan.
    """
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def get_devin() -> DevinClient:
    """The shared :class:`~orchestrator.devin.client.DevinClient`."""
    global _devin
    if _devin is None:
        settings = get_settings()
        # Fail here, naming the fix, rather than at the first session create
        # halfway through a run.
        api_key = settings.require_devin()
        _devin = DevinClient(
            api_key=api_key,
            api_base=settings.devin_api_base,
            max_parallel=settings.max_parallel_agents,
            org_id=settings.devin_org_id.strip() or None,
            model=settings.devin_model.strip(),
        )
    return _devin


async def get_run_or_404(run_id: str, db: Session = Depends(get_db)) -> Run:
    """Fetch a run by id or raise ``HTTPException(404)``."""
    run = repo.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


async def lifespan(app: Any) -> AsyncIterator[None]:
    """Startup/shutdown: create singletons, init the DB, close cleanly."""
    global _bus, _devin
    settings = get_settings()
    validate_paths(settings)
    init_db()

    _bus = get_bus()
    if settings.devin_api_key.strip():
        _devin = get_devin()
    else:
        log.warning("DEVIN_API_KEY unset; agent stages will fail until it is set")

    try:
        yield
    finally:
        if _devin is not None:
            try:
                await _devin.close()
            except Exception:  # shutdown must not raise out of the lifespan
                log.exception("closing the Devin client failed")
        _devin = None
