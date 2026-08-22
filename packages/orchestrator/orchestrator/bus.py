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

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

from orchestrator.schemas import Event, EventType

#: Events retained per run for late subscribers to replay.
REPLAY_BUFFER_SIZE = 500

#: Queue depth per subscriber before it is considered stalled and dropped.
SUBSCRIBER_QUEUE_SIZE = 256


class EventBus:
    """Async fan-out of run events to any number of subscribers."""

    def __init__(
        self,
        replay_size: int = REPLAY_BUFFER_SIZE,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._replay_size = replay_size
        self._clock = clock
        self._history: dict[str, deque[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Event | None]]] = {}
        self._seq: dict[str, int] = {}
        self._throttle_last: dict[tuple[str, str], float] = {}
        self._closed: set[str] = set()
        #: Runs something has subscribed to. A run nobody ever streamed keeps
        #: its buffer so a late REST catch-up still has something to return.
        self._streamed: set[str] = set()

    async def publish(self, event: Event) -> None:
        """Assign ``seq``, buffer, and fan out to subscribers. Never blocks."""
        run_id = event.run_id
        self._seq[run_id] = self._seq.get(run_id, 0) + 1
        event.seq = self._seq[run_id]

        history = self._history.get(run_id)
        if history is None:
            history = deque(maxlen=self._replay_size)
            self._history[run_id] = history
        history.append(event)

        stalled: list[asyncio.Queue[Event | None]] = []
        for queue in self._subscribers.get(run_id, set()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                stalled.append(queue)
        for queue in stalled:
            self._drop(run_id, queue)

    async def emit(self, run_id: str, type_: EventType, data: dict[str, Any]) -> Event:
        """Convenience: build an Event, publish it, return it.

        The form callers should use — keeps ``seq`` and ``ts`` out of caller code.
        """
        event = Event(run_id=run_id, type=type_, data=data)
        await self.publish(event)
        return event

    async def emit_throttled(
        self,
        run_id: str,
        type_: EventType,
        data: dict[str, Any],
        *,
        key: str,
        min_interval_s: float,
    ) -> Event | None:
        """Publish a side-channel event only when its interval has elapsed.

        Progress and frame notifications are a side channel: a dropped event is
        one nobody needed, whereas queueing it would starve state events and
        break the rule that publishing never blocks the pipeline. State events
        are always sent through :meth:`emit`, never this method.
        """
        now = self._clock()
        throttle_key = (run_id, key)
        previous = self._throttle_last.get(throttle_key)
        if (
            previous is not None
            and min_interval_s > 0
            and now - previous < min_interval_s
        ):
            return None
        self._throttle_last[throttle_key] = now
        return await self.emit(run_id, type_, data)

    async def subscribe(
        self, run_id: str, since: int | None = None
    ) -> AsyncIterator[Event]:
        """Yield events for ``run_id``, starting with replay from ``since``.

        ``since`` is the last ``seq`` the client saw; ``None`` replays the whole
        buffer. The iterator ends when the run reaches a terminal stage.
        """
        queue: asyncio.Queue[Event | None] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        self._subscribers.setdefault(run_id, set()).add(queue)
        self._streamed.add(run_id)
        last_seq = since or 0
        try:
            for event in self.history(run_id, since):
                last_seq = max(last_seq, event.seq)
                yield event
            if run_id in self._closed:
                return
            while True:
                event = await queue.get()
                if event is None:
                    return
                if event.seq <= last_seq:
                    # Already delivered by the replay pass above.
                    continue
                last_seq = event.seq
                yield event
        finally:
            self._drop(run_id, queue)

    def history(self, run_id: str, since: int | None = None) -> list[Event]:
        """Buffered events for a run, for the REST catch-up path."""
        events = self._history.get(run_id, ())
        if since is None:
            return list(events)
        return [event for event in events if event.seq > since]

    async def close(self, run_id: str) -> None:
        """Signal end-of-stream and release subscribers for a finished run."""
        self._closed.add(run_id)
        for throttle_key in tuple(self._throttle_last):
            if throttle_key[0] == run_id:
                self._throttle_last.pop(throttle_key, None)
        for queue in self._subscribers.pop(run_id, set()):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover - sentinel is best effort
                pass
        self._evict_if_idle(run_id)

    # -- internals --------------------------------------------------------- #

    def _drop(self, run_id: str, queue: asyncio.Queue[Event | None]) -> None:
        """Unregister one subscriber, evicting the run's buffer when idle."""
        subscribers = self._subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)
        self._evict_if_idle(run_id)

    def _evict_if_idle(self, run_id: str) -> None:
        """Free the replay buffer of a closed run once nobody is reading it.

        Retention policy: buffers of live runs are kept for late subscribers,
        buffers of finished runs only until the last subscriber disconnects.
        A run nobody ever streamed keeps its buffer — dropping it would strand
        the REST catch-up path with nothing to serve.
        """
        if (
            run_id in self._closed
            and run_id in self._streamed
            and not self._subscribers.get(run_id)
        ):
            self._history.pop(run_id, None)
            self._seq.pop(run_id, None)
            for throttle_key in tuple(self._throttle_last):
                if throttle_key[0] == run_id:
                    self._throttle_last.pop(throttle_key, None)
