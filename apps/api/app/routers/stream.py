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

from fastapi import APIRouter, WebSocket

router = APIRouter(tags=["stream"])


@router.websocket("/ws/runs/{run_id}")
async def run_stream(websocket: WebSocket, run_id: str, since: int = 0) -> None:
    """Stream one run's events until it finishes or the client disconnects."""
    raise NotImplementedError
    # TODO(build): accept(), subscribe to bus with since, async-for send_json
    # of event.model_dump(mode="json"), handle WebSocketDisconnect, always
    # unsubscribe in a finally block.


@router.websocket("/ws/runs")
async def index_stream(websocket: WebSocket) -> None:
    """Stream run-level events across all runs, for the index page.

    Only ``run.created``, ``run.stage_changed`` and ``run.finished`` — the index
    does not need per-scenario chatter from every active run.
    """
    raise NotImplementedError
    # TODO(build): subscribe to a global topic, filter to run.* event types.


# TODO(build): add a heartbeat ping every ~30s; idle WebSockets get closed by
# intermediaries and the dashboard will look frozen rather than disconnected.
