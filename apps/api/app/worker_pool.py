"""Derive the worker-pool read model for a run.

Responsibility
--------------
Combine durable scenario state with the latest in-process pool event so a
fresh dashboard load sees the same shape as a client that stayed connected.

Inputs:  a database session, run id, event bus, and runtime settings.
Outputs: a ``worker_pool`` mapping with pool counters and running scenarios.

The database is authoritative for ``busy`` and ``queued``. Those values are
measured from ``running`` and ``pending`` scenarios rather than copied from an
event that may have been missed during an API restart. ``workers`` and
``reason`` come from the newest ``worker.pool_changed`` event when available;
without one, ``workers`` falls back to ``max(sim_workers, busy)`` and
``reason`` is ``None``.
"""

from __future__ import annotations

from typing import Any

from orchestrator.bus import EventBus
from orchestrator.schemas import EventType, ScenarioStatus
from sqlmodel import Session

from app.config import get_settings
from app.store import repo


def get_worker_pool(db: Session, run_id: str, bus: EventBus) -> dict[str, Any]:
    """Return measured pool state plus the scenarios currently running."""
    scenarios = repo.list_scenarios(db, run_id)
    busy = sum(scenario.status is ScenarioStatus.RUNNING for scenario in scenarios)
    queued = sum(scenario.status is ScenarioStatus.PENDING for scenario in scenarios)

    pool_event = next(
        (
            event
            for event in reversed(bus.history(run_id))
            if event.type is EventType.WORKER_POOL_CHANGED
        ),
        None,
    )
    if pool_event is None:
        workers = max(get_settings().sim_workers, busy)
        reason = None
    else:
        workers = int(pool_event.data["workers"])
        reason = pool_event.data.get("reason")

    return {
        "workers": workers,
        "busy": busy,
        "queued": queued,
        "reason": reason,
        "scenarios": [
            scenario.model_dump(mode="json")
            for scenario in sorted(
                (s for s in scenarios if s.status is ScenarioStatus.RUNNING),
                key=lambda s: (s.index, s.attempt),
            )
        ],
    }
