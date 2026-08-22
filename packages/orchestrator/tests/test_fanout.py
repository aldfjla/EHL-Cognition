"""Deterministic tests for independent cluster workflows."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from orchestrator import pipeline as pipeline_mod
from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.pipeline import Pipeline, PipelineContext
from orchestrator.schemas import (
    Agent,
    EventType,
    Finding,
    FindingKind,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Speaker,
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
                id="s-a",
                index=0,
                seed=11,
                status=ScenarioStatus.FAILED,
                diagnosis="joint A stalls",
            ),
            Scenario(
                run_id=run.id,
                id="s-b",
                index=1,
                seed=22,
                status=ScenarioStatus.FAILED,
                diagnosis="joint B stalls",
            ),
        ],
    )


def result(scenario_id: str, seed: int, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        scenario_id=scenario_id,
        seed=seed,
        status=status,
        duration_s=0.0,
        sim_time_s=0.0,
        criteria=[],
        diagnosis=None,
        video_path=None,
        trace_path=None,
        error=None,
    )


async def test_cluster_b_verifies_while_cluster_a_is_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    pipe._max_parallel = 2
    pipe._agent_gate = asyncio.Semaphore(2)
    await pipe.stage_cluster_failures()
    assert len(ctx.clusters) == 2

    a_started = asyncio.Event()
    release_a = asyncio.Event()
    b_resolved = asyncio.Event()
    verify_seeds: list[list[int]] = []

    class Investigator:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None

        async def dispatch(self, **kwargs: object) -> Agent:
            cluster_id = str(kwargs["cluster_id"])
            if cluster_id == next(
                cluster.id
                for cluster in ctx.clusters
                if cluster.label.startswith("joint a")
            ):
                a_started.set()
                await release_a.wait()
            agent = Agent(run_id=ctx.run.id, role=Role.INVESTIGATOR)
            finding = Finding(
                run_id=ctx.run.id,
                author_agent_id=agent.id,
                author_role=Speaker.INVESTIGATOR,
                kind=FindingKind.ROOT_CAUSE,
                summary=f"cause {cluster_id}",
                cluster_id=cluster_id,
                confidence=0.9,
            )
            await ctx.blackboard.write(finding)
            agent.finding_ids.append(finding.id)
            return agent

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def merge_patches(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        verify_seeds.append([scenario.seed for scenario in scenarios])
        if [scenario.seed for scenario in scenarios] == [22]:
            b_resolved.set()
        return [result(scenario.id, scenario.seed, "passed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod, "InvestigatorAgent", Investigator)
    monkeypatch.setattr(pipeline_mod, "FixerAgent", Fixer)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "create_worktree", create_worktree)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)

    pipe._start_cluster_workflows()
    await a_started.wait()
    await b_resolved.wait()
    a_work = next(
        work
        for work in pipe._cluster_work.values()
        if work.cluster.label.startswith("joint a")
    )
    assert a_work.phase == "investigating"
    assert [22] in verify_seeds

    release_a.set()
    await asyncio.gather(*pipe._cluster_tasks.values())

    assert all(work.outcome == "resolved" for work in pipe._cluster_work.values())


async def test_fixer_claim_does_not_resolve_failed_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()
    work = next(iter(pipe._cluster_work.values()))
    agent = Agent(run_id=ctx.run.id, role=Role.INVESTIGATOR)
    cause = Finding(
        run_id=ctx.run.id,
        author_agent_id=agent.id,
        author_role=Speaker.INVESTIGATOR,
        kind=FindingKind.ROOT_CAUSE,
        summary="claimed cause",
        cluster_id=work.cluster.id,
        confidence=0.9,
    )
    await ctx.blackboard.write(cause)
    work.cause = cause
    work.phase = "root_cause"

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def merge_patches(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "failed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod, "FixerAgent", Fixer)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "create_worktree", create_worktree)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)

    await pipe._fix_cluster(work)
    await pipe._verify_cluster(work)

    assert work.phase == "unresolved"
    assert work.outcome == "unresolved"


async def test_one_fixer_failure_does_not_cancel_other_clusters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()

    class Investigator:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None

        async def dispatch(self, **kwargs: object) -> Agent:
            agent = Agent(run_id=ctx.run.id, role=Role.INVESTIGATOR)
            finding = Finding(
                run_id=ctx.run.id,
                author_agent_id=agent.id,
                author_role=Speaker.INVESTIGATOR,
                kind=FindingKind.ROOT_CAUSE,
                summary="cause",
                cluster_id=str(kwargs["cluster_id"]),
                confidence=0.9,
            )
            await ctx.blackboard.write(finding)
            agent.finding_ids.append(finding.id)
            return agent

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **kwargs: object) -> Agent:
            if str(kwargs["cluster_id"]) == ctx.clusters[0].id:
                raise RuntimeError("fixer failed")
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def merge_patches(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "passed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod, "InvestigatorAgent", Investigator)
    monkeypatch.setattr(pipeline_mod, "FixerAgent", Fixer)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "create_worktree", create_worktree)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)

    pipe._start_cluster_workflows()
    await asyncio.gather(*pipe._cluster_tasks.values())

    assert pipe._cluster_work[ctx.clusters[0].id].phase == "unresolved"
    assert pipe._cluster_work[ctx.clusters[1].id].phase == "resolved"


async def test_agent_gate_bounds_investigators_and_fixers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    ctx.scenarios.append(
        Scenario(
            run_id=ctx.run.id,
            id="s-c",
            index=2,
            seed=33,
            status=ScenarioStatus.FAILED,
            diagnosis="joint C stalls",
        )
    )
    pipe = Pipeline(ctx)
    pipe._max_parallel = 1
    pipe._agent_gate = asyncio.Semaphore(1)
    await pipe.stage_cluster_failures()
    active = 0
    maximum = 0
    release = asyncio.Event()

    class Investigator:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None

        async def dispatch(self, **kwargs: object) -> Agent:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await release.wait()
            active -= 1
            agent = Agent(run_id=ctx.run.id, role=Role.INVESTIGATOR)
            finding = Finding(
                run_id=ctx.run.id,
                author_agent_id=agent.id,
                author_role=Speaker.INVESTIGATOR,
                kind=FindingKind.ROOT_CAUSE,
                summary="cause",
                cluster_id=str(kwargs["cluster_id"]),
                confidence=0.9,
            )
            await ctx.blackboard.write(finding)
            agent.finding_ids.append(finding.id)
            return agent

    class Fixer:
        def __init__(self, _ctx: PipelineContext) -> None:
            self.session = None
            self.output = {"patched": True}

        async def dispatch(self, **_kwargs: object) -> Agent:
            return Agent(run_id=ctx.run.id, role=Role.FIXER)

    async def create_worktree(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "fix"

    async def merge_patches(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "passed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod, "InvestigatorAgent", Investigator)
    monkeypatch.setattr(pipeline_mod, "FixerAgent", Fixer)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "create_worktree", create_worktree)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)
    pipe._start_cluster_workflows()
    await asyncio.sleep(0.005)
    assert maximum == 1
    release.set()
    await asyncio.gather(*pipe._cluster_tasks.values())


async def test_conflicting_applies_are_serialized_and_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()
    works = list(pipe._cluster_work.values())
    for work in works:
        work.phase = "ready_to_verify"
        work.worktree = f"fix-{work.cluster.id}"
    active = 0
    maximum = 0

    async def merge_patches(
        _workspace: Workspace, worktrees: list[str], *, into: str
    ) -> list[str]:
        nonlocal active, maximum
        del into
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.001)
        active -= 1
        return worktrees if worktrees == [works[0].worktree] else []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "passed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)

    await asyncio.gather(*(pipe._verify_cluster(work) for work in works))
    await pipe._retry_conflicted_clusters()

    assert maximum == 1
    assert works[0].phase == "unresolved"
    assert works[1].phase == "resolved"


async def test_agent_updated_contains_only_new_optional_fields(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    agent = Agent(
        run_id=ctx.run.id,
        role=Role.INVESTIGATOR,
        session_url="https://app.devin.ai/sessions/1",
    )
    role = SimpleNamespace(
        session=SimpleNamespace(
            agent=agent,
            handle=SimpleNamespace(desktop_url="https://desktop.example/1"),
        )
    )

    await pipe._emit_agent_update(role, issue="oracle issue", step="investigating")  # type: ignore[arg-type]
    await pipe._emit_agent_update(role, issue="oracle issue", step="fixing")  # type: ignore[arg-type]

    updates = [
        event
        for event in ctx.bus.history(ctx.run.id)
        if event.type is EventType.AGENT_UPDATED
    ]
    assert updates[0].data == {
        "agent_id": agent.id,
        "session_url": "https://app.devin.ai/sessions/1",
        "desktop_url": "https://desktop.example/1",
        "issue": "oracle issue",
        "step": "investigating",
    }
    assert updates[1].data == {"agent_id": agent.id, "step": "fixing"}
