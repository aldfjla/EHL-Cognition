"""AgentSession lifecycle, event emission and the relay registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeBus
from orchestrator.devin.session import (
    AgentSession,
    file_transcript_sink,
    live_sessions,
    set_transcript_sink,
)
from orchestrator.schemas import AgentStatus, Role


class FakeClient:
    """Records the calls AgentSession makes, with no HTTP."""

    def __init__(
        self, lines: list[str] | None = None, status: str = "finished"
    ) -> None:
        self.lines = lines or ["reading the repo", "done"]
        self.status = status
        self.created: list[dict[str, Any]] = []
        self.messages: list[str] = []

    async def create_session(self, prompt: str, **kwargs: Any) -> Any:
        self.created.append({"prompt": prompt, **kwargs})
        return type("H", (), {"session_id": "s-1", "url": "u-1", "status": "working"})

    async def wait_until_done(self, session_id: str, **kwargs: Any) -> dict:
        on_activity = kwargs.get("on_activity")
        for line in self.lines:
            if on_activity is not None:
                await on_activity(line)
        return {"status": self.status, "messages": []}

    async def send_message(self, session_id: str, message: str) -> None:
        self.messages.append(message)

    async def structured_output(self, session_id: str) -> dict:
        return {"root_cause": "timer"}


async def start(bus: FakeBus, client: FakeClient) -> AgentSession:
    return await AgentSession.start(
        run_id="run-1",
        role=Role.INVESTIGATOR,
        prompt="find it",
        title="Investigator — gripper",
        task="investigate",
        client=client,
        bus=bus,
        cluster_id="cls-1",
    )


async def test_start_emits_created_before_the_api_call(bus: FakeBus) -> None:
    client = FakeClient()
    session = await start(bus, client)

    assert bus.types()[0] == "agent.created"
    # The card exists before the session id does, so the grid is never blank.
    assert bus.events[0][2]["status"] == AgentStatus.STARTING.value
    assert session.agent.session_id == "s-1"
    assert session.agent.session_url == "u-1"
    assert session.agent.status is AgentStatus.WORKING
    assert client.created[0]["tags"] == ["run:run-1", "role:investigator"]


async def test_wait_streams_activity_and_tracks_last_line(bus: FakeBus) -> None:
    session = await start(bus, FakeClient(lines=["step one", "step two"]))
    await session.wait(timeout_s=1.0)

    assert session.transcript == ["step one", "step two"]
    assert session.agent.last_activity == "step two"
    assert bus.types().count("agent.activity") == 2


async def test_blocked_session_is_reported_as_blocked(bus: FakeBus) -> None:
    session = await start(bus, FakeClient(status="blocked"))
    await session.wait(timeout_s=1.0)
    assert session.agent.status is AgentStatus.BLOCKED


async def test_failed_wait_marks_the_agent_failed(bus: FakeBus) -> None:
    class Boom(FakeClient):
        async def wait_until_done(self, session_id: str, **kwargs: Any) -> dict:
            raise RuntimeError("transport down")

    session = await start(bus, Boom())
    with pytest.raises(RuntimeError):
        await session.wait(timeout_s=1.0)
    assert session.agent.status is AgentStatus.FAILED
    assert session.agent.finished_at is not None


async def test_ask_relays_and_records_the_message(bus: FakeBus) -> None:
    client = FakeClient()
    session = await start(bus, client)
    await session.ask("please emit json")

    assert client.messages == ["please emit json"]
    assert "message.sent" in bus.types()


async def test_registry_holds_live_sessions_only(bus: FakeBus) -> None:
    session = await start(bus, FakeClient())
    assert live_sessions("run-1", Role.INVESTIGATOR) == [session]
    assert live_sessions("run-1") == [session]

    await session.set_status(AgentStatus.SUCCEEDED)
    assert live_sessions("run-1", Role.INVESTIGATOR) == []


async def test_output_delegates_to_the_client(bus: FakeBus) -> None:
    session = await start(bus, FakeClient())
    assert await session.output() == {"root_cause": "timer"}


async def test_file_transcript_sink_persists_lines(
    bus: FakeBus, tmp_path: Path
) -> None:
    set_transcript_sink(file_transcript_sink(tmp_path))
    try:
        session = await start(bus, FakeClient(lines=["a", "b"]))
        await session.wait(timeout_s=1.0)
    finally:
        set_transcript_sink(None)

    written = (tmp_path / f"{session.agent.id}.jsonl").read_text(encoding="utf-8")
    records = [json.loads(line) for line in written.splitlines()]
    assert [r["text"] for r in records] == ["a", "b"]
