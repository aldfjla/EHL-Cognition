"""Tests for bounded fixer trees and oracle-owned verification."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.devin.hierarchy import AgentTree
from orchestrator.pipeline import Pipeline, PipelineContext, _ClusterWork
from orchestrator.roles.fixer import FixerAgent
from orchestrator.schemas import (
    Agent,
    Cluster,
    EventType,
    Finding,
    FindingKind,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Speaker,
    SuiteStats,
)
from orchestrator.workspace import Workspace


def make_context(tmp_path: Path) -> PipelineContext:
    run = Run(repo="acme/arm-control", commit_sha="a" * 40)
    return PipelineContext(
        run=run,
        workspace=Workspace(
            run_id=run.id,
            repo=run.repo,
            commit_sha=run.commit_sha,
            root=tmp_path,
        ),
        bus=EventBus(),
        blackboard=Blackboard(run.id),
        devin=None,  # type: ignore[arg-type]
        scenarios=[
            Scenario(
                run_id=run.id,
                id="red",
                index=0,
                seed=11,
                status=ScenarioStatus.FAILED,
            )
        ],
    )


def result(scenario: Scenario, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        scenario_id=scenario.id,
        seed=scenario.seed,
        status=status,
        duration_s=0.0,
        sim_time_s=0.0,
        criteria=[],
        diagnosis=None,
        video_path=None,
        trace_path=None,
        error=None,
    )


def work_for(ctx: PipelineContext, owner_id: str) -> tuple[Pipeline, object]:
    pipe = Pipeline(ctx)
    cluster = Cluster(
        run_id=ctx.run.id,
        id="cluster-1",
        label="joint failure",
        scenario_ids=["red"],
        size=1,
    )
    ctx.clusters = [cluster]
    work = _ClusterWork(
        cluster=cluster,
        original_seeds=[11],
        owner_agent_id=owner_id,
    )
    pipe._cluster_work[cluster.id] = work
    cause = Finding(
        run_id=ctx.run.id,
        author_agent_id=owner_id,
        author_role=Speaker.INVESTIGATOR,
        kind=FindingKind.ROOT_CAUSE,
        summary="joint timer stalls",
        cluster_id=cluster.id,
        confidence=0.9,
    )
    work.cause = cause
    pipe._agent_tree.register_root(owner_id)
    return pipe, work


class FakeClient:
    async def create_session(self, _prompt: str, **_kwargs: object) -> object:
        return SimpleNamespace(session_id="session", url="session://one")

    async def wait_until_done(self, _session_id: str, **_kwargs: object) -> dict:
        return {"status": "finished", "messages": []}

    async def structured_output(self, _session_id: str) -> dict:
        return {"diff_summary": "patched", "patched": True}


async def test_role_dispatch_emits_parent_link(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    ctx.devin = FakeClient()  # type: ignore[assignment]
    agent = await FixerAgent(ctx).dispatch(
        parent_agent_id="owner-1", cluster_id="cluster-1"
    )

    created = [
        event
        for event in ctx.bus.history(ctx.run.id)
        if event.type is EventType.AGENT_CREATED
    ]
    assert created[0].data["parent_agent_id"] == "owner-1"
    assert agent.parent_agent_id == "owner-1"


async def test_pipeline_children_link_to_owner_and_fixer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe, work = work_for(ctx, "investigator-1")
    calls: list[tuple[str, str | None]] = []

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **kwargs: object) -> Agent:
            calls.append(("fixer", kwargs.get("parent_agent_id")))  # type: ignore[arg-type]
            return Agent(
                run_id=ctx.run.id, role=Role.FIXER, parent_agent_id="investigator-1"
            )

    class Reviewer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"verdict": "ship"}

        async def dispatch(self, **kwargs: object) -> Agent:
            calls.append(("reviewer", kwargs.get("parent_agent_id")))  # type: ignore[arg-type]
            return Agent(
                run_id=ctx.run.id, role=Role.REVIEWER, parent_agent_id="fixer-1"
            )

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario, "passed") for scenario in scenarios]

    monkeypatch.setattr("orchestrator.pipeline.FixerAgent", Fixer)
    monkeypatch.setattr("orchestrator.pipeline.ReviewerAgent", Reviewer)
    monkeypatch.setattr(
        "orchestrator.pipeline.workspace_mod.create_worktree", create_worktree
    )
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

    await pipe._fix_cluster(work)

    assert calls == [("fixer", "investigator-1"), ("reviewer", work.agent_ids[0])]
    assert pipe._agent_tree.children("investigator-1") == (work.agent_ids[0],)
    assert pipe._agent_tree.children(work.agent_ids[0]) == (work.agent_ids[1],)


async def test_depth_cap_refusal_is_visible_on_cluster_and_bus(
    tmp_path: Path,
) -> None:
    ctx = make_context(tmp_path)
    pipe, work = work_for(ctx, "investigator-1")
    pipe._agent_tree = AgentTree(max_depth=1)

    await pipe._fix_cluster(work)

    assert work.phase == "unresolved"
    assert work.outcome == "unresolved"
    assert "MAX_AGENT_TREE_DEPTH=1" in (work.error or "")
    errors = [
        event for event in ctx.bus.history(ctx.run.id) if event.type is EventType.ERROR
    ]
    assert errors and errors[0].data["fatal"] is False


async def test_pipeline_fan_out_cap_refusal_is_visible_on_cluster_and_bus(
    tmp_path: Path,
) -> None:
    ctx = make_context(tmp_path)
    pipe, work = work_for(ctx, "investigator-1")
    pipe._agent_tree = AgentTree(max_children=1)
    pipe._agent_tree.register_root("investigator-1")
    pipe._agent_tree.register_child("investigator-1", "existing-fixer")

    await pipe._fix_cluster(work)

    assert work.phase == "unresolved"
    assert work.outcome == "unresolved"
    assert "MAX_AGENT_CHILDREN=1" in (work.error or "")
    errors = [
        event for event in ctx.bus.history(ctx.run.id) if event.type is EventType.ERROR
    ]
    assert errors and errors[0].data["fatal"] is False
    assert "MAX_AGENT_CHILDREN=1" in errors[0].data["message"]


async def test_reviewer_cap_refusal_keeps_simkit_green_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe, work = work_for(ctx, "investigator-1")
    pipe._agent_tree = AgentTree(max_depth=2)
    reviewer_dispatches = 0

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    class Reviewer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"verdict": "ship"}

        async def dispatch(self, **_kwargs: object) -> Agent:
            nonlocal reviewer_dispatches
            reviewer_dispatches += 1
            return Agent(run_id=ctx.run.id, role=Role.REVIEWER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario, "passed") for scenario in scenarios]

    monkeypatch.setattr("orchestrator.pipeline.FixerAgent", Fixer)
    monkeypatch.setattr("orchestrator.pipeline.ReviewerAgent", Reviewer)
    monkeypatch.setattr(
        "orchestrator.pipeline.workspace_mod.create_worktree", create_worktree
    )
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

    await pipe._fix_cluster(work)

    assert reviewer_dispatches == 0
    assert work.phase == "ready_to_verify"
    assert work.outcome is None
    assert work.worktree == "fix-cluster-1"
    assert pipe._fix_worktrees == ["fix-cluster-1"]
    assert "MAX_AGENT_TREE_DEPTH=2" in (work.error or "")
    errors = [
        event for event in ctx.bus.history(ctx.run.id) if event.type is EventType.ERROR
    ]
    assert errors and errors[0].data["fatal"] is False
    assert "MAX_AGENT_TREE_DEPTH=2" in errors[0].data["message"]


def test_resolved_advisory_error_is_omitted_from_unresolved_reason(
    tmp_path: Path,
) -> None:
    ctx = make_context(tmp_path)
    pipe, resolved_work = work_for(ctx, "investigator-1")
    resolved_work.outcome = "resolved"
    resolved_work.error = "Reviewer seat was refused advisory-only"
    unresolved_work = _ClusterWork(
        cluster=Cluster(
            run_id=ctx.run.id,
            id="cluster-2",
            label="still failing",
            scenario_ids=["red"],
            size=1,
        ),
        original_seeds=[11],
        outcome="unresolved",
        error="originally red seed stayed red",
    )
    pipe._cluster_work[unresolved_work.cluster.id] = unresolved_work

    reason = pipe._verification_failure_reason(
        SuiteStats.from_counts(passed=1, failed=0), {}
    )

    assert "Reviewer seat was refused" not in reason
    assert "still failing: originally red seed stayed red" in reason


def test_agent_tree_refuses_depth_and_fan_out() -> None:
    tree = AgentTree(max_depth=3, max_children=2)
    tree.register_root("owner")
    tree.register_child("owner", "fixer")
    tree.register_child("owner", "spare")
    assert "MAX_AGENT_CHILDREN=2" in (tree.child_refusal("owner") or "")
    tree.register_child("fixer", "reviewer")
    assert "MAX_AGENT_TREE_DEPTH=3" in (tree.child_refusal("reviewer") or "")


async def test_red_seeds_reject_fix_even_when_reviewer_claims_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe, work = work_for(ctx, "investigator-1")

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    class Reviewer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"verdict": "ship"}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.REVIEWER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario, "failed") for scenario in scenarios]

    monkeypatch.setattr("orchestrator.pipeline.FixerAgent", Fixer)
    monkeypatch.setattr("orchestrator.pipeline.ReviewerAgent", Reviewer)
    monkeypatch.setattr(
        "orchestrator.pipeline.workspace_mod.create_worktree", create_worktree
    )
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

    await pipe._fix_cluster(work)

    assert work.phase == "unresolved"
    assert work.outcome == "unresolved"
    assert "11" in (work.error or "")
    assert "Reviewer claimed success" in (work.error or "")
    assert pipe._fix_worktrees == []


def test_render_two_cluster_tree() -> None:
    tree = AgentTree()
    tree.register_root("investigator-cluster-a")
    tree.register_child("investigator-cluster-a", "fixer-cluster-a")
    tree.register_child("fixer-cluster-a", "reviewer-cluster-a")
    tree.register_root("investigator-cluster-b")
    tree.register_child("investigator-cluster-b", "fixer-cluster-b")
    tree.register_child("fixer-cluster-b", "reviewer-cluster-b")
    assert tree.render() == (
        "investigator-cluster-a\n"
        "  fixer-cluster-a\n"
        "    reviewer-cluster-a\n"
        "investigator-cluster-b\n"
        "  fixer-cluster-b\n"
        "    reviewer-cluster-b"
    )
