"""``GET /runs/{id}/agents`` and ``/agents/{id}/messages`` — the team view.

Responsibility
--------------
Serve the agent roster and the relayed message traffic that
``AgentGrid``, ``TeamChat`` and ``AgentGraph`` render.

Inputs:  a run id or an agent id.
Outputs: :class:`~orchestrator.schemas.Agent` and
         :class:`~orchestrator.schemas.Message` payloads.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.deps import get_db, get_run_or_404
from app.store import repo

router = APIRouter(tags=["agents"])


@router.get("/runs/{run_id}/agents")
async def list_agents(
    run_id: str, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Every agent in a run, in creation order.

    Creation order matters: it is the order the pipeline dispatched them, so
    rendering it straight reads as a timeline of how the team assembled.
    """
    await get_run_or_404(run_id, db)
    return [agent.model_dump(mode="json") for agent in repo.list_agents(db, run_id)]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """One agent, including its Devin session url for click-through."""
    agent = repo.get_agent(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent.model_dump(mode="json")


@router.get("/agents/{agent_id}/messages")
async def agent_messages(
    agent_id: str, limit: int = 200, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Messages relayed to or from this agent, oldest first."""
    if repo.get_agent(db, agent_id) is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return [
        message.model_dump(mode="json")
        for message in repo.list_messages(db, agent_id=agent_id, limit=limit)
    ]


@router.get("/runs/{run_id}/messages")
async def run_messages(
    run_id: str, limit: int = 200, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """The whole team's traffic. Backs the TeamChat feed on first paint."""
    await get_run_or_404(run_id, db)
    return [
        message.model_dump(mode="json")
        for message in repo.list_messages(db, run_id=run_id, limit=limit)
    ]


@router.get("/runs/{run_id}/findings")
async def run_findings(
    run_id: str, status: str | None = None, db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """The blackboard contents — what the team currently believes."""
    await get_run_or_404(run_id, db)
    return [
        finding.model_dump(mode="json")
        for finding in repo.list_findings(db, run_id, status)
    ]
