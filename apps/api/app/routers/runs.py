"""``GET /runs`` and ``GET /runs/{id}`` — run list and detail.

Responsibility
--------------
Serve the run objects the dashboard renders. Read-only; runs are created by
the webhook router and mutated only by the pipeline.

Inputs:  query params for paging and filtering.
Outputs: :class:`~orchestrator.schemas.Run` payloads, plus the run's scenarios
         on the detail route so the matrix can render without a second call.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from orchestrator.bus import EventBus
from orchestrator.schemas import Event
from sqlmodel import Session

from app import events
from app.deps import get_bus, get_db, get_run_or_404
from app.store import repo

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
async def list_runs(
    limit: int = 25, offset: int = 0, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Runs newest first. Powers the dashboard index page."""
    return [run.model_dump(mode="json") for run in repo.list_runs(db, limit, offset)]


@router.get("/{run_id}")
async def get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """One run with its scenarios, clusters and report id.

    Returns everything the mission control page needs for first paint; the
    WebSocket then keeps it current. Deliberately one round trip — a dashboard
    that fires six requests on load flickers through six loading states.
    """
    run = await get_run_or_404(run_id, db)
    return {
        **run.model_dump(mode="json"),
        "scenarios": [
            s.model_dump(mode="json") for s in repo.list_scenarios(db, run_id)
        ],
        "clusters": [c.model_dump(mode="json") for c in repo.list_clusters(db, run_id)],
    }


@router.get("/{run_id}/scenarios")
async def list_scenarios(
    run_id: str, attempt: int | None = None, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """The scenario matrix. ``attempt`` selects the baseline or a VERIFY re-run."""
    await get_run_or_404(run_id, db)
    return [
        scenario.model_dump(mode="json")
        for scenario in repo.list_scenarios(db, run_id, attempt)
    ]


@router.get("/{run_id}/clusters")
async def list_clusters(
    run_id: str, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Failure clusters, largest first. One cluster is one Investigator."""
    await get_run_or_404(run_id, db)
    return [
        cluster.model_dump(mode="json") for cluster in repo.list_clusters(db, run_id)
    ]


@router.get("/{run_id}/events")
async def get_events(
    run_id: str, since: int = 0, bus: EventBus = Depends(get_bus)
) -> list[dict[str, Any]]:
    """Replay buffered events. The catch-up path when the WebSocket drops."""
    return [event.model_dump(mode="json") for event in bus.history(run_id, since)]


@router.post("/{run_id}/events", status_code=202)
async def ingest_event(
    run_id: str, payload: dict[str, Any], bus: EventBus = Depends(get_bus)
) -> dict[str, Any]:
    """Inject an event into this process's bus. Development and replay only.

    The bus is in-process, so ``scripts/seed_mock_run.py`` — a separate
    process — has no other way to drive a live dashboard. Events arrive already
    shaped by ``event.json``; ``seq`` is re-stamped by the bus so a replay never
    collides with a real run's numbering.
    """
    event = Event.model_validate({**payload, "run_id": run_id})
    await events.publish(bus, event)
    return {"accepted": event.type.value, "seq": event.seq}


@router.get("/{run_id}/report")
async def get_report(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The incident report, once REPORT has completed."""
    await get_run_or_404(run_id, db)
    report = repo.get_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not written yet")
    return report.model_dump(mode="json")
