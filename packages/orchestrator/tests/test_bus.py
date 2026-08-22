"""Bus ordering, replay and slow-subscriber behaviour."""

from __future__ import annotations

import asyncio

from orchestrator.bus import SUBSCRIBER_QUEUE_SIZE, EventBus
from orchestrator.schemas import Event, EventType

RUN = "run_test"


async def _emit(bus: EventBus, count: int, run_id: str = RUN) -> None:
    for i in range(count):
        await bus.emit(run_id, EventType.SUITE_PROGRESS, {"completed": i})


async def test_seq_is_monotonic_and_per_run() -> None:
    bus = EventBus()
    await _emit(bus, 3, "run_a")
    await _emit(bus, 2, "run_b")

    assert [e.seq for e in bus.history("run_a")] == [1, 2, 3]
    assert [e.seq for e in bus.history("run_b")] == [1, 2]


async def test_history_since_returns_only_later_events() -> None:
    bus = EventBus()
    await _emit(bus, 5)

    assert [e.seq for e in bus.history(RUN, since=3)] == [4, 5]
    assert [e.seq for e in bus.history(RUN, since=0)] == [1, 2, 3, 4, 5]
    assert bus.history(RUN, since=5) == []


async def test_replay_buffer_is_bounded() -> None:
    bus = EventBus(replay_size=4)
    await _emit(bus, 10)

    replayed = bus.history(RUN)
    assert [e.seq for e in replayed] == [7, 8, 9, 10]


async def test_subscribe_replays_then_streams_live() -> None:
    bus = EventBus()
    await _emit(bus, 2)

    seen: list[int] = []

    async def reader() -> None:
        async for event in bus.subscribe(RUN, since=None):
            seen.append(event.seq)
            if event.seq == 4:
                return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)  # let the reader drain the replay
    await _emit(bus, 2)
    await asyncio.wait_for(task, timeout=2)

    assert seen == [1, 2, 3, 4]


async def test_subscribe_since_skips_replayed_events() -> None:
    bus = EventBus()
    await _emit(bus, 3)

    seen: list[int] = []

    async def reader() -> None:
        async for event in bus.subscribe(RUN, since=2):
            seen.append(event.seq)
            if event.seq == 4:
                return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    await _emit(bus, 1)
    await asyncio.wait_for(task, timeout=2)

    assert seen == [3, 4]


async def test_no_event_is_delivered_twice_across_the_replay_seam() -> None:
    """A live event published while replay is draining must arrive once."""
    bus = EventBus()
    await _emit(bus, 1)

    seen: list[int] = []
    stream = bus.subscribe(RUN, since=None)
    # Publish before pulling anything: the event lands in both the replay
    # buffer and the subscriber queue.
    await _emit(bus, 1)
    for _ in range(2):
        seen.append((await anext(stream)).seq)
    await stream.aclose()

    assert seen == [1, 2]


async def test_publish_never_blocks_on_a_stalled_subscriber() -> None:
    bus = EventBus()
    await _emit(bus, 1)
    stream = bus.subscribe(RUN, since=None)
    # Drain the replay so the queue is registered, then stop reading entirely.
    assert (await anext(stream)).seq == 1

    # Far more than one queue can hold; publish must stay responsive.
    await asyncio.wait_for(_emit(bus, SUBSCRIBER_QUEUE_SIZE + 50), timeout=5)
    assert bus.history(RUN)[-1].seq == SUBSCRIBER_QUEUE_SIZE + 51
    await stream.aclose()


async def test_close_terminates_open_streams() -> None:
    bus = EventBus()
    await _emit(bus, 1)

    seen: list[int] = []

    async def reader() -> None:
        async for event in bus.subscribe(RUN, since=None):
            seen.append(event.seq)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    await bus.close(RUN)
    await asyncio.wait_for(task, timeout=2)

    assert seen == [1]


async def test_publish_assigns_seq_even_if_the_caller_set_one() -> None:
    bus = EventBus()
    event = Event(run_id=RUN, type=EventType.RUN_CREATED, data={}, seq=99)
    await bus.publish(event)
    assert event.seq == 1


async def test_emit_throttled_drops_inside_window_and_passes_after() -> None:
    now = [10.0]
    bus = EventBus(clock=lambda: now[0])

    first = await bus.emit_throttled(
        RUN,
        EventType.SCENARIO_PROGRESS,
        {},
        key="scenario.progress:s1",
        min_interval_s=1.0,
    )
    now[0] += 0.5
    dropped = await bus.emit_throttled(
        RUN,
        EventType.SCENARIO_PROGRESS,
        {},
        key="scenario.progress:s1",
        min_interval_s=1.0,
    )
    now[0] += 0.5
    second = await bus.emit_throttled(
        RUN,
        EventType.SCENARIO_PROGRESS,
        {},
        key="scenario.progress:s1",
        min_interval_s=1.0,
    )

    assert first is not None
    assert dropped is None
    assert second is not None
    assert [event.seq for event in bus.history(RUN)] == [1, 2]


async def test_concurrent_emitters_keep_seq_contiguous() -> None:
    bus = EventBus()

    async def emit_many() -> None:
        for _ in range(25):
            await bus.emit_throttled(
                RUN,
                EventType.SCENARIO_PROGRESS,
                {},
                key=str(asyncio.current_task()),
                min_interval_s=0,
            )

    await asyncio.gather(*(emit_many() for _ in range(8)))

    assert [event.seq for event in bus.history(RUN)] == list(range(1, 201))
