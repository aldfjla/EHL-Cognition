"""WebSocket contract from ``docs/EVENT_PROTOCOL.md``.

What matters to the dashboard: one event per frame, ``seq`` monotonic, replay
honours ``?since=``, and the socket closes itself after ``run.finished``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from orchestrator.schemas import Event, EventType
from starlette.websockets import WebSocketDisconnect

from app.routers.stream import WS_TRY_AGAIN_LATER

from .conftest import FakeBus

RUN_ID = "run_stream"


def seed(bus: FakeBus, *types: EventType) -> None:
    """Publish ``types`` for RUN_ID, letting the bus assign seq."""

    async def publish() -> None:
        for type_ in types:
            await bus.publish(Event(run_id=RUN_ID, type=type_, data={}))

    asyncio.run(publish())


def test_replay_then_close_on_finished(client: TestClient, bus: FakeBus) -> None:
    seed(
        bus,
        EventType.RUN_CREATED,
        EventType.RUN_STAGE_CHANGED,
        EventType.SCENARIO_FINISHED,
        EventType.RUN_FINISHED,
    )

    with client.websocket_connect(f"/ws/runs/{RUN_ID}") as ws:
        frames = [ws.receive_json() for _ in range(4)]

    assert [frame["seq"] for frame in frames] == [1, 2, 3, 4]
    assert [frame["type"] for frame in frames] == [
        "run.created",
        "run.stage_changed",
        "scenario.finished",
        "run.finished",
    ]
    # Envelope shape is the wire contract in packages/contracts/schemas/event.json.
    assert set(frames[0]) == {"id", "run_id", "seq", "type", "ts", "data"}
    assert frames[0]["run_id"] == RUN_ID


def test_since_skips_what_the_client_already_saw(
    client: TestClient, bus: FakeBus
) -> None:
    seed(
        bus,
        EventType.RUN_CREATED,
        EventType.RUN_STAGE_CHANGED,
        EventType.RUN_FINISHED,
    )

    with client.websocket_connect(f"/ws/runs/{RUN_ID}?since=2") as ws:
        frame = ws.receive_json()

    assert frame["seq"] == 3
    assert frame["type"] == "run.finished"


def test_live_events_are_forwarded_after_replay(
    client: TestClient, bus: FakeBus
) -> None:
    seed(bus, EventType.RUN_CREATED)

    with client.websocket_connect(f"/ws/runs/{RUN_ID}") as ws:
        assert ws.receive_json()["type"] == "run.created"
        seed(bus, EventType.SUITE_PROGRESS, EventType.RUN_FINISHED)
        assert ws.receive_json()["type"] == "suite.progress"
        assert ws.receive_json()["type"] == "run.finished"


def test_index_stream_filters_to_run_level_events(
    client: TestClient, bus: FakeBus
) -> None:
    async def publish() -> None:
        for type_ in (
            EventType.RUN_CREATED,
            EventType.MESSAGE_SENT,
            EventType.RUN_FINISHED,
        ):
            await bus.publish(Event(run_id="*", type=type_, data={}))

    asyncio.run(publish())

    with client.websocket_connect("/ws/runs") as ws:
        assert ws.receive_json()["type"] == "run.created"
        assert ws.receive_json()["type"] == "run.finished"


def test_failing_subscriber_closes_with_1013(
    client: TestClient, bus: FakeBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped subscriber is reported as "reconnect with ?since=", not a crash."""

    async def boom(run_id: str, since: int | None = None) -> None:
        raise RuntimeError("subscriber dropped")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(bus, "subscribe", boom)

    with (
        client.websocket_connect(f"/ws/runs/{RUN_ID}") as ws,
        pytest.raises(WebSocketDisconnect) as caught,
    ):
        ws.receive_json()

    assert caught.value.code == WS_TRY_AGAIN_LATER
