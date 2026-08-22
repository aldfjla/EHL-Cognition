"""``WS /ws/runs/{id}`` — the live event feed the dashboard runs on.

Responsibility
--------------
Bridge :mod:`orchestrator.bus` to a browser. Subscribe on connect, forward every
:class:`~orchestrator.schemas.Event` as JSON, replay what the client missed, and
clean up on disconnect.

Inputs:  a run id, an optional ``?since=<seq>`` cursor.
Outputs: a stream of Event JSON objects, newline-delimited per WS frame.

Protocol summary (full spec in ``docs/EVENT_PROTOCOL.md``)
----------------------------------------------------------
* On connect the server replays buffered events after ``since``, then streams
  live. A client reconnecting sends the last ``seq`` it saw and loses nothing.
* Every frame is one Event. No batching — a batched frame complicates the
  client for no benefit at this event volume.
* ``seq`` is monotonic per run. A gap means the client fell behind and should
  refetch ``GET /runs/{id}`` rather than trying to reconstruct state.
* The server closes the socket after ``run.finished``.

Backpressure
------------
A browser tab that stops reading must not stall the pipeline. The bus drops
slow subscribers; this router notices the drop and closes with a code the client
treats as "reconnect with ?since=".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterable

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from orchestrator.bus import EventBus
from orchestrator.schemas import Event, EventType

from app.deps import get_bus
from app.events import INDEX_EVENT_TYPES, INDEX_TOPIC

log = logging.getLogger("robotci.stream")

router = APIRouter(tags=["stream"])

#: Close code telling the client "you fell behind, reconnect with ?since=".
WS_TRY_AGAIN_LATER = 1013

#: Seconds between heartbeat frames. Idle sockets get reaped by proxies well
#: inside a long SIMULATE stage, and a dashboard that stops updating looks
#: frozen rather than disconnected.
HEARTBEAT_SECONDS = 30.0


async def _pump(
    websocket: WebSocket,
    stream: AsyncIterator[Event],
    *,
    keep: Iterable[EventType] | None = None,
    close_on_finished: bool,
) -> None:
    """Forward ``stream`` to ``websocket`` one event per frame.

    Heartbeats are interleaved by racing each ``anext`` against a timeout, so a
    quiet run still produces traffic without a second task writing to the same
    socket concurrently.
    """
    allowed = frozenset(keep) if keep is not None else None
    iterator = stream.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(iterator.__anext__(), HEARTBEAT_SECONDS)
        except StopAsyncIteration:
            return
        except TimeoutError:
            await websocket.send_json({"type": "heartbeat"})
            continue

        if allowed is not None and event.type not in allowed:
            continue

        await websocket.send_json(event.model_dump(mode="json"))
        if close_on_finished and event.type is EventType.RUN_FINISHED:
            return


async def _aclose(stream: AsyncIterator[Event]) -> None:
    """Release the subscriber queue behind an async-generator stream."""
    if isinstance(stream, AsyncGenerator):
        await stream.aclose()


@router.websocket("/ws/runs/{run_id}")
async def run_stream(
    websocket: WebSocket,
    run_id: str,
    since: int = 0,
    bus: EventBus = Depends(get_bus),
) -> None:
    """Stream one run's events until it finishes or the client disconnects."""
    await websocket.accept()
    stream = bus.subscribe(run_id, since or None)
    try:
        await _pump(websocket, stream, close_on_finished=True)
    except WebSocketDisconnect:
        return
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        log.exception("stream for run %s failed", run_id)
        await _close(websocket, WS_TRY_AGAIN_LATER)
        return
    finally:
        await _aclose(stream)

    await _close(websocket, 1000)


@router.websocket("/ws/runs")
async def index_stream(websocket: WebSocket, bus: EventBus = Depends(get_bus)) -> None:
    """Stream run-level events across all runs, for the index page.

    Only ``run.created``, ``run.stage_changed`` and ``run.finished`` — the index
    does not need per-scenario chatter from every active run.
    """
    await websocket.accept()
    stream = bus.subscribe(INDEX_TOPIC, None)
    try:
        await _pump(websocket, stream, keep=INDEX_EVENT_TYPES, close_on_finished=False)
    except WebSocketDisconnect:
        return
    except (asyncio.CancelledError, GeneratorExit):
        raise
    except Exception:
        log.exception("index stream failed")
        await _close(websocket, WS_TRY_AGAIN_LATER)
        return
    finally:
        await _aclose(stream)

    await _close(websocket, 1000)


async def _close(websocket: WebSocket, code: int) -> None:
    """Close politely; a client that already vanished is not an error."""
    try:
        await websocket.close(code=code)
    except RuntimeError:
        pass
