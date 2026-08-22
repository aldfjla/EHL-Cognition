"""One session's lifecycle and transcript, wrapped as an Agent.

Responsibility
--------------
Bridge between :class:`~orchestrator.devin.client.DevinClient` (HTTP, sessions)
and the rest of the system (roles, findings, events). Holds the mapping from a
Devin session to our :class:`~orchestrator.schemas.Agent` record and keeps the
dashboard informed as the session progresses.

Inputs:  a rendered prompt, a role, a run context.
Outputs: an ``Agent`` row that stays current, ``agent.*`` events on the bus,
         and the session's parsed structured output.
"""

from __future__ import annotations

from typing import Any

from orchestrator.schemas import Agent, AgentStatus, Role


class AgentSession:
    """A Devin session bound to a role and a run."""

    def __init__(
        self,
        agent: Agent,
        client: Any,
        bus: Any,
    ) -> None:
        self.agent = agent
        self.client = client
        self.bus = bus

    @classmethod
    async def start(
        cls,
        *,
        run_id: str,
        role: Role,
        prompt: str,
        title: str,
        task: str,
        client: Any,
        bus: Any,
        **agent_fields: Any,
    ) -> AgentSession:
        """Create the Agent record, emit ``agent.created``, start the session.

        The Agent row exists *before* the API call so the dashboard shows a
        card in ``starting`` immediately — a five-second blank grid is the
        difference between the demo looking alive and looking hung.
        """
        raise NotImplementedError
        # TODO(build): build Agent, emit agent.created, call
        # client.create_session, store session_id/url, emit status change.

    async def wait(self, timeout_s: float = 1800.0) -> dict[str, Any]:
        """Await completion, streaming transcript lines to the bus."""
        raise NotImplementedError
        # TODO(build): client.wait_until_done with on_activity ->
        # emit agent.activity and update agent.last_activity.

    async def ask(self, message: str) -> None:
        """Relay a message into this session and record it on the bus."""
        raise NotImplementedError
        # TODO(build): client.send_message + emit message.sent.

    async def set_status(self, status: AgentStatus, detail: str = "") -> None:
        """Update status and emit ``agent.status_changed``."""
        raise NotImplementedError
        # TODO(build): mutate, stamp updated_at/finished_at, publish.

    async def output(self) -> dict[str, Any]:
        """Parsed structured output from the session."""
        raise NotImplementedError
        # TODO(build): delegate to client.structured_output.


# TODO(build): add a transcript-persisting hook so finished sessions can be
# replayed into the dashboard after the API restarts.
