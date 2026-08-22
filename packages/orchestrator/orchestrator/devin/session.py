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

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.schemas import (
    Agent,
    AgentStatus,
    EventType,
    Message,
    MessageKind,
    Role,
    Speaker,
)

#: Live, non-terminal sessions keyed by ``(run_id, role)``.
#:
#: Devin sessions cannot address each other, so a relay has to find the
#: recipient's session object. The registry is that lookup — see
#: ``orchestrator.roles.base.RoleAgent.relay``.
_LIVE: dict[tuple[str, Role], list[AgentSession]] = {}

#: Optional sink invoked with ``(agent, line)`` for every transcript line.
#:
#: Set by the API process to persist transcripts, so a finished session can be
#: replayed into the dashboard after a restart.
_TRANSCRIPT_SINK: Callable[[Agent, str], Any] | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def live_sessions(run_id: str, role: Role | None = None) -> list[AgentSession]:
    """Sessions of ``run_id`` that have not reached a terminal status."""
    if role is not None:
        return list(_LIVE.get((run_id, role), ()))
    out: list[AgentSession] = []
    for (rid, _role), sessions in _LIVE.items():
        if rid == run_id:
            out.extend(sessions)
    return out


def set_transcript_sink(sink: Callable[[Agent, str], Any] | None) -> None:
    """Install (or clear) the hook that persists transcript lines."""
    global _TRANSCRIPT_SINK
    _TRANSCRIPT_SINK = sink


def file_transcript_sink(directory: Path) -> Callable[[Agent, str], None]:
    """A :func:`set_transcript_sink` sink appending JSONL under ``directory``.

    One file per agent, so a finished run can be replayed into the dashboard
    after the API restarts.
    """
    directory = Path(directory)

    def sink(agent: Agent, line: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        record = {"agent_id": agent.id, "ts": _now().isoformat(), "text": line}
        path = directory / f"{agent.id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    return sink


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
        #: Every transcript line seen, oldest first.
        self.transcript: list[str] = []

    # -- registry ---------------------------------------------------------- #

    @property
    def _key(self) -> tuple[str, Role]:
        return (self.agent.run_id, self.agent.role)

    def _register(self) -> None:
        _LIVE.setdefault(self._key, []).append(self)

    def _unregister(self) -> None:
        sessions = _LIVE.get(self._key)
        if not sessions:
            return
        if self in sessions:
            sessions.remove(self)
        if not sessions:
            _LIVE.pop(self._key, None)

    # -- lifecycle --------------------------------------------------------- #

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
        agent = Agent(
            run_id=run_id,
            role=role,
            title=title,
            task=task,
            status=AgentStatus.STARTING,
            **agent_fields,
        )
        session = cls(agent, client, bus)
        session._register()
        await bus.emit(run_id, EventType.AGENT_CREATED, agent.model_dump(mode="json"))

        try:
            handle = await client.create_session(
                prompt,
                title=title,
                tags=[f"run:{run_id}", f"role:{role.value}"],
            )
        except Exception as exc:
            await session.set_status(AgentStatus.FAILED, str(exc))
            raise

        agent.session_id = handle.session_id
        agent.session_url = handle.url
        await session.set_status(AgentStatus.WORKING)
        return session

    async def wait(self, timeout_s: float = 1800.0) -> dict[str, Any]:
        """Await completion, streaming transcript lines to the bus."""
        if not self.agent.session_id:
            raise RuntimeError(f"agent {self.agent.id} has no session to wait on")

        async def on_activity(line: str) -> None:
            self.transcript.append(line)
            self.agent.last_activity = line
            self.agent.updated_at = _now()
            if _TRANSCRIPT_SINK is not None:
                _TRANSCRIPT_SINK(self.agent, line)
            await self.bus.emit(
                self.agent.run_id,
                EventType.AGENT_ACTIVITY,
                {
                    "agent_id": self.agent.id,
                    "text": line,
                    "ts": self.agent.updated_at.isoformat(),
                },
            )

        try:
            payload = await self.client.wait_until_done(
                self.agent.session_id,
                timeout_s=timeout_s,
                on_activity=on_activity,
            )
        except Exception as exc:
            await self.set_status(AgentStatus.FAILED, str(exc))
            raise

        status = str(payload.get("status_enum") or payload.get("status") or "").lower()
        if status == "blocked":
            await self.set_status(AgentStatus.BLOCKED, "session is waiting on input")
        return payload

    async def ask(self, message: str) -> None:
        """Relay a message into this session and record it on the bus."""
        if not self.agent.session_id:
            raise RuntimeError(f"agent {self.agent.id} has no session to message")
        await self.client.send_message(self.agent.session_id, message)
        relay = Message(
            run_id=self.agent.run_id,
            to_agent_id=self.agent.id,
            from_role=Speaker.ORCHESTRATOR,
            to_role=Speaker(self.agent.role.value),
            kind=MessageKind.ANSWER,
            body=message,
        )
        await self.bus.emit(
            self.agent.run_id,
            EventType.MESSAGE_SENT,
            relay.model_dump(mode="json"),
        )

    async def set_status(self, status: AgentStatus, detail: str = "") -> None:
        """Update status and emit ``agent.status_changed``."""
        previous = self.agent.status
        self.agent.status = status
        self.agent.updated_at = _now()
        if status.is_terminal:
            self.agent.finished_at = self.agent.updated_at
            self._unregister()
        data: dict[str, Any] = {
            "agent_id": self.agent.id,
            "status": status.value,
            "previous_status": previous.value,
            "session_id": self.agent.session_id,
            "session_url": self.agent.session_url,
        }
        if detail:
            data["detail"] = detail
        if status.is_terminal and self.agent.finished_at is not None:
            data["finished_at"] = self.agent.finished_at.isoformat()
        await self.bus.emit(self.agent.run_id, EventType.AGENT_STATUS_CHANGED, data)

    async def output(self) -> dict[str, Any]:
        """Parsed structured output from the session."""
        if not self.agent.session_id:
            raise RuntimeError(f"agent {self.agent.id} produced no session")
        return await self.client.structured_output(self.agent.session_id)
