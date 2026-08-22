"""In-process async pub/sub carrying run events to WebSocket subscribers.

Responsibility
--------------
Decouple the pipeline from the transport. The pipeline publishes typed
:class:`~orchestrator.schemas.Event` objects; ``apps/api/app/routers/stream.py``
subscribes on behalf of each connected dashboard and forwards them as JSON.

Inputs:  ``Event`` objects from the pipeline, roles and simkit callbacks.
Outputs: fan-out to every subscriber of that run, plus a bounded replay buffer
so a dashboard opened mid-run can catch up.

Design notes
------------
* One bus per API process, keyed by ``run_id``. Deliberately in-memory: with
  no Docker and a single process, Redis would be ceremony. The seam is here if
  a second process ever needs it.
* Publishing must never block the pipeline. Slow subscribers get dropped, not
  awaited — a stalled browser tab must not stall a robot CI run.
* ``seq`` is assigned here, not by callers, so ordering is authoritative.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from orchestrator.schemas import Event, EventType

#: Events retained per run for late subscribers to replay.
REPLAY_BUFFER_SIZE = 500

#: Queue depth per subscriber before it is considered stalled and dropped.
SUBSCRIBER_QUEUE_SIZE = 256


class EventBus:
    """Async fan-out of run events to any number of subscribers."""

    def __init__(self, replay_size: int = REPLAY_BUFFER_SIZE) -> None:
        self._replay_size = replay_size
        # TODO(build): dict[run_id, deque[Event]] history and
        # dict[run_id, set[asyncio.Queue[Event]]] subscribers.

    async def publish(self, event: Event) -> None:
        """Assign ``seq``, buffer, and fan out to subscribers. Never blocks."""
        raise NotImplementedError
        # TODO(build): stamp seq, append to replay deque, put_nowait to each
        # subscriber queue, drop subscribers whose queue is full.

    async def emit(self, run_id: str, type_: EventType, data: dict[str, Any]) -> Event:
        """Convenience: build an Event, publish it, return it.

        The form callers should use — keeps ``seq`` and ``ts`` out of caller code.
        """
        raise NotImplementedError
        # TODO(build): construct Event(run_id=..., type=type_, data=data), publish.

    def subscribe(self, run_id: str, since: int | None = None) -> AsyncIterator[Event]:
        """Yield events for ``run_id``, starting with replay from ``since``.

        ``since`` is the last ``seq`` the client saw; ``None`` replays the whole
        buffer. The iterator ends when the run reaches a terminal stage.
        """
        raise NotImplementedError
        # TODO(build): async generator — drain replay past `since`, then yield
        # from a fresh queue; unregister the queue in a finally block.

    def history(self, run_id: str, since: int | None = None) -> list[Event]:
        """Buffered events for a run, for the REST catch-up path."""
        raise NotImplementedError
        # TODO(build): slice the replay deque by seq.

    async def close(self, run_id: str) -> None:
        """Signal end-of-stream and release subscribers for a finished run."""
        raise NotImplementedError
        # TODO(build): push a sentinel to each queue, clear registrations.


# TODO(build): decide the retention policy for replay buffers of finished runs —
# right now they would leak for the process lifetime. Evict on run.finished
# after the last subscriber disconnects.
