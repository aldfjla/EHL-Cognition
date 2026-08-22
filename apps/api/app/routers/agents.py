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

from fastapi import APIRouter

router = APIRouter(tags=["agents"])


@router.get("/runs/{run_id}/agents")
async def list_agents(run_id: str) -> list[dict[str, Any]]:
    """Every agent in a run, in creation order.

    Creation order matters: it is the order the pipeline dispatched them, so
    rendering it straight reads as a timeline of how the team assembled.
    """
    raise NotImplementedError
    # TODO(build): repo.list_agents(run_id).


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """One agent, including its Devin session url for click-through."""
    raise NotImplementedError
    # TODO(build): repo.get_agent; 404 on miss.


@router.get("/agents/{agent_id}/messages")
async def agent_messages(agent_id: str) -> list[dict[str, Any]]:
    """Messages relayed to or from this agent, oldest first."""
    raise NotImplementedError
    # TODO(build): repo.list_messages(agent_id=...).


@router.get("/runs/{run_id}/messages")
async def run_messages(run_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """The whole team's traffic. Backs the TeamChat feed on first paint."""
    raise NotImplementedError
    # TODO(build): repo.list_messages(run_id=...) ordered by ts.


@router.get("/runs/{run_id}/findings")
async def run_findings(run_id: str) -> list[dict[str, Any]]:
    """The blackboard contents — what the team currently believes."""
    raise NotImplementedError
    # TODO(build): repo.list_findings(run_id).
