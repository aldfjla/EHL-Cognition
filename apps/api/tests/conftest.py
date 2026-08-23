"""Shared fixtures: an isolated store, a working bus, and a client.

Two things make these tests independent of the machine they run on:

* every test gets its own SQLite file, so nothing inherits ``robotci.db``;
* the event bus is a small in-memory double. ``orchestrator.bus.EventBus`` is
  owned by another slice and still stubbed, and the WebSocket contract is
  testable against its documented interface without waiting for it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from orchestrator.schemas import Event, EventType, Run, Stage

from app import config, deps
from app.main import create_app
from app.store import db as store_db
from app.store import repo


class FakeBus:
    """A working stand-in for :class:`orchestrator.bus.EventBus`.

    Implements the documented surface — ``publish``/``emit``/``subscribe``/
    ``history``/``close`` — with the ordering guarantee the protocol relies on:
    ``seq`` is assigned here, never by callers.
    """

    def __init__(self) -> None:
        self.history_by_run: dict[str, list[Event]] = {}
        self._queues: dict[str, list[asyncio.Queue[Event | None]]] = {}
        self._seq: dict[str, int] = {}

    async def publish(self, event: Event) -> None:
        self._seq[event.run_id] = self._seq.get(event.run_id, 0) + 1
        stamped = event.model_copy(update={"seq": self._seq[event.run_id]})
        self.history_by_run.setdefault(event.run_id, []).append(stamped)
        for queue in self._queues.get(event.run_id, []):
            queue.put_nowait(stamped)

    async def emit(self, run_id: str, type_: EventType, data: dict[str, Any]) -> Event:
        event = Event(run_id=run_id, type=type_, data=data)
        await self.publish(event)
        return self.history_by_run[run_id][-1]

    async def subscribe(
        self, run_id: str, since: int | None = None
    ) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues.setdefault(run_id, []).append(queue)
        try:
            for event in self.history(run_id, since):
                yield event
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self._queues.get(run_id, []).remove(queue)

    def history(self, run_id: str, since: int | None = None) -> list[Event]:
        events = self.history_by_run.get(run_id, [])
        if since is None:
            return list(events)
        return [event for event in events if event.seq > since]

    async def close(self, run_id: str) -> None:
        for queue in self._queues.get(run_id, []):
            queue.put_nowait(None)


@pytest.fixture(autouse=True)
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.Settings:
    """Point every setting at ``tmp_path`` and reset the cached singletons."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("MENAGERIE_DIR", str(tmp_path / "menagerie"))
    monkeypatch.setenv("WEBHOOK_SECRET", "shhh")
    monkeypatch.setenv("TARGET_REPO", "acme/robot")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    monkeypatch.setenv("DEVIN_API_KEY", "")
    # Pin the origin the webhook URL is built from. Tests assert on it, so
    # inheriting a developer's .env makes them fail whenever the local API
    # runs on anything but the default port.
    monkeypatch.setenv("API_ORIGIN", "http://localhost:8000")

    config.get_settings.cache_clear()
    store_db.reset_engine()
    try:
        yield config.get_settings()
    finally:
        config.get_settings.cache_clear()
        store_db.reset_engine()


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def client(bus: FakeBus) -> Iterator[TestClient]:
    """A client on an app whose bus is the fake and whose DB is temporary."""
    app = create_app()
    app.dependency_overrides[deps.get_bus] = lambda: bus
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def db() -> Iterator[Any]:
    """A session on the temporary database, schema already created."""
    store_db.init_db()
    with store_db.session_scope() as session:
        yield session


@pytest.fixture
def run(db: Any) -> Run:
    """One persisted run to hang REST assertions off.

    Committed immediately: the app opens its own session, so an uncommitted row
    would be invisible to every request.
    """
    run = repo.create_run(
        db,
        Run(
            repo="acme/robot",
            branch="main",
            commit_sha="a" * 40,
            commit_message="tune the grasp",
            pushed_by="ada",
            stage=Stage.RUN_SUITE,
        ),
    )
    db.commit()
    return run
