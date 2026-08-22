"""Publishing helpers around :class:`~orchestrator.bus.EventBus`.

Responsibility
--------------
Two things the transport layer needs and the bus deliberately does not do:

* **Mirror run-level events onto the index topic.** The bus is keyed by
  ``run_id``, but ``WS /ws/runs`` (the index page) wants ``run.*`` across every
  run. Rather than teach the bus about topics it does not own, anything the API
  publishes for a run is also published under :data:`INDEX_TOPIC`.
* **Never let a publish failure lose a run.** A webhook that 500s because the
  dashboard's transport hiccuped has thrown away the only notification GitHub
  will send us. Publishing is best-effort and logged; persistence is not.
"""

from __future__ import annotations

import logging
from typing import Any

from orchestrator.bus import EventBus
from orchestrator.schemas import Event, EventType

log = logging.getLogger("robotci.events")

#: Pseudo run id carrying run-level events for the index page's socket.
INDEX_TOPIC = "*"

#: Event types the index page cares about.
INDEX_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.RUN_CREATED, EventType.RUN_STAGE_CHANGED, EventType.RUN_FINISHED}
)


async def emit(
    bus: EventBus, run_id: str, type_: EventType, data: dict[str, Any]
) -> Event | None:
    """Publish one event, mirroring run-level types to the index topic.

    Returns the published event, or ``None`` if publishing failed.
    """
    event: Event | None = None
    try:
        event = await bus.emit(run_id, type_, data)
    except Exception:
        log.exception("publishing %s for %s failed", type_, run_id)

    if type_ in INDEX_EVENT_TYPES and run_id != INDEX_TOPIC:
        try:
            await bus.emit(INDEX_TOPIC, type_, {**data, "run_id": run_id})
        except Exception:
            log.exception("mirroring %s to the index topic failed", type_)

    return event


async def publish(bus: EventBus, event: Event) -> None:
    """Publish an already-built event (the seeded-replay ingest path)."""
    try:
        await bus.publish(event)
    except Exception:
        log.exception("publishing %s failed", event.type)
        return

    if event.type in INDEX_EVENT_TYPES and event.run_id != INDEX_TOPIC:
        try:
            await bus.emit(
                INDEX_TOPIC, event.type, {**event.data, "run_id": event.run_id}
            )
        except Exception:
            log.exception("mirroring %s to the index topic failed", event.type)
