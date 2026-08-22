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

from fastapi import APIRouter

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
async def list_runs(limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
    """Runs newest first. Powers the dashboard index page."""
    raise NotImplementedError
    # TODO(build): repo.list_runs(limit, offset) -> model_dump(mode="json").


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    """One run with its scenarios, clusters and report id.

    Returns everything the mission control page needs for first paint; the
    WebSocket then keeps it current. Deliberately one round trip — a dashboard
    that fires six requests on load flickers through six loading states.
    """
    raise NotImplementedError
    # TODO(build): repo.get_run + list_scenarios + list_clusters; 404 on miss.


@router.get("/{run_id}/events")
async def get_events(run_id: str, since: int = 0) -> list[dict[str, Any]]:
    """Replay buffered events. The catch-up path when the WebSocket drops."""
    raise NotImplementedError
    # TODO(build): bus.history(run_id, since).


@router.get("/{run_id}/report")
async def get_report(run_id: str) -> dict[str, Any]:
    """The incident report, once REPORT has completed."""
    raise NotImplementedError
    # TODO(build): repo.get_report_for_run; 404 while the run is still going.
