"""Fakes for the collaborators the agent layer talks to.

``bus.py``, ``blackboard.py`` and ``workspace.py`` belong to another slice and
are still stubs, so the tests here stand in for them with the same signatures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from orchestrator.devin import session as session_module
from orchestrator.pipeline import PipelineContext
from orchestrator.schemas import Finding, FindingKind, FindingStatus, Role, Run


class FakeBus:
    """Records what a real EventBus would publish."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def emit(self, run_id: str, type_: Any, data: dict[str, Any]) -> None:
        self.events.append((run_id, getattr(type_, "value", str(type_)), data))

    def types(self) -> list[str]:
        return [type_ for _, type_, _ in self.events]


class FakeBlackboard:
    """Collects findings and renders a trivial context block."""

    def __init__(self, run_id: str = "run-1") -> None:
        self.run_id = run_id
        self.findings: list[Finding] = []
        self.confirmed: list[str] = []
        self.refuted: list[tuple[str, str]] = []
        self.superseded: list[tuple[str, str]] = []

    async def write(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    async def confirm(self, finding_id: str, by_agent_id: str) -> None:
        self.confirmed.append(finding_id)

    async def refute(self, finding_id: str, reason: str) -> None:
        self.refuted.append((finding_id, reason))

    async def supersede(self, old_id: str, new: Finding) -> None:
        self.superseded.append((old_id, new.id))

    def all(self) -> list[Finding]:
        return list(self.findings)

    def for_cluster(self, cluster_id: str) -> list[Finding]:
        return [f for f in self.findings if f.cluster_id == cluster_id]

    def for_role(self, role: Role) -> list[Finding]:
        return [f for f in self.findings if f.author_role.value == role.value]

    def constraints(self) -> list[Finding]:
        return [f for f in self.findings if f.kind is FindingKind.CONSTRAINT]

    def confirmed_root_causes(self) -> list[Finding]:
        return [
            f
            for f in self.findings
            if f.kind is FindingKind.ROOT_CAUSE and f.status is FindingStatus.CONFIRMED
        ]

    def render_context(self, role: Role, cluster_id: str | None = None) -> str:
        return f"context for {role.value}"


class FakeWorkspace:
    """Just enough of Workspace for the roles' template variables."""

    def __init__(self, base: Path) -> None:
        self._base = base

    @property
    def base(self) -> Path:
        return self._base

    def worktree(self, name: str) -> Path:
        return self._base / "worktrees" / name


@pytest.fixture(autouse=True)
def clean_session_registry() -> Any:
    """The live-session registry is module state; don't leak it between tests."""
    session_module._LIVE.clear()
    yield
    session_module._LIVE.clear()


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def blackboard() -> FakeBlackboard:
    return FakeBlackboard()


@pytest.fixture
def ctx(tmp_path: Path, bus: FakeBus, blackboard: FakeBlackboard) -> PipelineContext:
    """A PipelineContext wired to fakes, with no Devin client."""
    run = Run(repo="acme/arm-control", commit_sha="deadbeef", branch="main")
    return PipelineContext(
        run=run,
        workspace=FakeWorkspace(tmp_path),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        blackboard=blackboard,  # type: ignore[arg-type]
        devin=None,  # type: ignore[arg-type]
        config={
            "control": {
                "entrypoint": "src/main.py",
                "interface": "joint_position",
                "rate_hz": 100,
            },
            "robot": {"menagerie": "franka_emika_panda"},
            "task": {"name": "pick_place", "success_criteria": ["cube lifted"]},
        },
    )
