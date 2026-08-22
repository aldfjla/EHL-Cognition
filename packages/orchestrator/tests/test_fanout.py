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
from orchestrator.pool import SuitePool
from orchestrator.schemas import (
    Agent,
    EventType,
    Finding,
    FindingKind,
    ModelSource,
    RobotModel,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Speaker,
)
from orchestrator.workspace import PatchConflict, Workspace


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

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
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
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

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

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
        return []

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "failed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod, "FixerAgent", Fixer)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "create_worktree", create_worktree)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

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

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
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
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

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

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
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
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)
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
        _workspace: Workspace,
        worktrees: list[str],
        *,
        into: str,
        landed_worktrees: list[str],
    ) -> list[PatchConflict]:
        nonlocal active, maximum
        del into, landed_worktrees
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.001)
        active -= 1
        if worktrees != [works[0].worktree]:
            return []
        return [
            PatchConflict(
                worktree=works[0].worktree,
                branch="test",
                sha="a" * 40,
                files=("control.py",),
                blocked_by=("sibling",),
            )
        ]

    async def execute_suite(
        scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(scenario.id, scenario.seed, "passed") for scenario in scenarios]

    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_suite)

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


async def test_cluster_verification_is_read_only_and_has_no_suite_progress(
    tmp_path: Path,
) -> None:
    ctx = make_context(tmp_path)
    ctx.run.robot_model = RobotModel(
        source=ModelSource.REPO,
        model_path="model.xml",
    )
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()
    work = next(iter(pipe._cluster_work.values()))
    work.phase = "ready_to_verify"
    work.worktree = "fix-cluster"
    scenario = ctx.scenarios[0]
    scenario.video_path = "before.mp4"
    before = scenario.model_copy(deep=True)
    observed: list[dict[str, object]] = []

    async def runner(**kwargs: object) -> SimpleNamespace:
        observed.append(kwargs)
        return result(str(kwargs["scenario_id"]), int(kwargs["seed"]), "passed")

    pipe._pool = SuitePool(
        run_id=ctx.run.id,
        bus=ctx.bus,
        workers=2,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        original_merge = pipeline_mod.workspace_mod.merge_patches

        async def merge_patches(
            *_args: object, **_kwargs: object
        ) -> list[PatchConflict]:
            return []

        pipeline_mod.workspace_mod.merge_patches = merge_patches
        try:
            await pipe._verify_cluster(work)
        finally:
            pipeline_mod.workspace_mod.merge_patches = original_merge
    finally:
        await pipe._pool.aclose()

    assert work.phase == "resolved"
    assert [int(item["seed"]) for item in observed] == [scenario.seed, scenario.seed]
    assert observed[0]["record"] is False
    assert isinstance(observed[1]["record"], str)
    assert scenario == before
    assert not any(
        event.type in {EventType.SCENARIO_FINISHED, EventType.SUITE_PROGRESS}
        for event in ctx.bus.history(ctx.run.id)
    )


async def test_after_video_is_a_distinct_recording_of_a_passing_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    ctx.run.robot_model = RobotModel(source=ModelSource.REPO, model_path="model.xml")
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()
    work = next(iter(pipe._cluster_work.values()))
    work.phase = "ready_to_verify"
    work.worktree = "fix-cluster"
    before = tmp_path / "before.mp4"
    before.write_bytes(b"before")
    ctx.scenarios[0].video_path = str(before)
    observed: list[dict[str, object]] = []

    async def runner(**kwargs: object) -> SimpleNamespace:
        observed.append(kwargs)
        record_path = kwargs.get("record")
        if record_path:
            path = Path(str(record_path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"after")
        item = result(str(kwargs["scenario_id"]), int(kwargs["seed"]), "passed")
        item.video_path = str(record_path) if record_path else None
        return item

    async def execute_cluster_suite(
        _scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(ctx.scenarios[0].id, ctx.scenarios[0].seed, "passed")]

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
        return []

    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_cluster_suite)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    pipe._pool = SuitePool(
        run_id=ctx.run.id,
        bus=ctx.bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        await pipe._verify_cluster(work)
    finally:
        await pipe._pool.aclose()

    incident = pipe._incident(work.cluster)
    assert incident.before_video == str(before)
    assert incident.after_video is not None
    assert incident.after_video != incident.before_video
    assert Path(incident.after_video).is_file()
    assert len(observed) == 1
    assert observed[0]["record"] != incident.before_video


async def test_after_video_is_unavailable_when_post_fix_seed_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_context(tmp_path)
    ctx.run.robot_model = RobotModel(source=ModelSource.REPO, model_path="model.xml")
    pipe = Pipeline(ctx)
    await pipe.stage_cluster_failures()
    work = next(iter(pipe._cluster_work.values()))
    work.phase = "ready_to_verify"
    work.worktree = "fix-cluster"
    before = tmp_path / "before.mp4"
    before.write_bytes(b"before")
    ctx.scenarios[0].video_path = str(before)

    async def runner(**kwargs: object) -> SimpleNamespace:
        record_path = kwargs.get("record")
        item = result(str(kwargs["scenario_id"]), int(kwargs["seed"]), "failed")
        item.video_path = str(record_path) if record_path else None
        return item

    async def execute_cluster_suite(
        _scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return [result(ctx.scenarios[0].id, ctx.scenarios[0].seed, "passed")]

    async def merge_patches(*_args: object, **_kwargs: object) -> list[PatchConflict]:
        return []

    monkeypatch.setattr(pipe, "_execute_cluster_suite", execute_cluster_suite)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "merge_patches", merge_patches)
    pipe._pool = SuitePool(
        run_id=ctx.run.id,
        bus=ctx.bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        await pipe._verify_cluster(work)
    finally:
        await pipe._pool.aclose()

    incident = pipe._incident(work.cluster)
    assert incident.before_video == str(before)
    assert incident.after_video is None


async def test_agent_policy_does_not_resize_suite_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_PARALLEL_AGENTS", "4")
    ctx = make_context(tmp_path)
    ctx.workspace.base.mkdir(parents=True)
    (ctx.workspace.base / "control.py").write_text("")
    pipe = Pipeline(ctx)

    async def clone(*_args: object, **_kwargs: object) -> Workspace:
        return ctx.workspace

    async def read_config(_workspace: Workspace) -> dict[str, object]:
        return {
            "policy": {"max_parallel_agents": 1},
            "control": {"entrypoint": "control:main"},
            "task": {},
        }

    async def set_status(*_args: object, **_kwargs: object) -> None:
        return None

    async def runner(**kwargs: object) -> SimpleNamespace:
        return result(str(kwargs["scenario_id"]), int(kwargs["seed"]), "passed")

    monkeypatch.setattr(pipeline_mod.workspace_mod, "clone", clone)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "read_config", read_config)
    monkeypatch.setattr(pipeline_mod.github, "set_commit_status", set_status)
    await pipe.stage_triggered()
    ctx.run.robot_model = RobotModel(
        source=ModelSource.REPO,
        model_path="model.xml",
    )
    pipe._pool = SuitePool(
        run_id=ctx.run.id,
        bus=ctx.bus,
        workers=pipe._max_parallel,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        await pipe._execute_suite([ctx.scenarios[0]])
        assert pipe._max_parallel == 4
        assert pipe._max_parallel_agents == 1
        assert pipe._pool.snapshot()["workers"] == 4
    finally:
        await pipe._pool.aclose()


async def test_agent_watcher_publishes_session_fields_before_dispatch_finishes(
    tmp_path: Path,
) -> None:
    ctx = make_context(tmp_path)
    pipe = Pipeline(ctx)
    ready = asyncio.Event()
    release = asyncio.Event()
    agent = Agent(run_id=ctx.run.id, role=Role.INVESTIGATOR)

    class RoleStub:
        session: SimpleNamespace | None = None

        async def dispatch(self, **_kwargs: object) -> Agent:
            self.session = SimpleNamespace(
                agent=agent,
                handle=SimpleNamespace(desktop_url="desktop://one"),
            )
            agent.session_url = "session://one"
            ready.set()
            await release.wait()
            self.session.handle.desktop_url = "desktop://two"
            agent.step = "waiting"
            return agent

    role = RoleStub()
    dispatch = asyncio.create_task(
        pipe._dispatch_with_agent_watch(
            role, issue="oracle issue", step="investigating"
        )  # type: ignore[arg-type]
    )
    await ready.wait()
    for _ in range(20):
        if any(
            event.type is EventType.AGENT_UPDATED
            for event in ctx.bus.history(ctx.run.id)
        ):
            break
        await asyncio.sleep(0)
    updates_before_release = [
        event
        for event in ctx.bus.history(ctx.run.id)
        if event.type is EventType.AGENT_UPDATED
    ]
    assert updates_before_release
    assert updates_before_release[0].data["session_url"] == "session://one"
    release.set()
    await dispatch
    updates = [
        event
        for event in ctx.bus.history(ctx.run.id)
        if event.type is EventType.AGENT_UPDATED
    ]
    assert any(event.data.get("desktop_url") == "desktop://two" for event in updates)
    assert any(event.data.get("step") == "waiting" for event in updates)
