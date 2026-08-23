"""The stage machine: legal transitions, event emission, failure handling."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from orchestrator import pipeline as pipeline_mod
from orchestrator import workspace as workspace_mod
from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.pipeline import (
    TRANSITIONS,
    Pipeline,
    PipelineContext,
    PipelineError,
    can_transition,
)
from orchestrator.schemas import (
    TERMINAL_STAGES,
    Agent,
    EventType,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Stage,
    SuiteStats,
)
from orchestrator.workspace import PatchConflict, Workspace
from simkit.models import resolver as resolver_module


def make_ctx(tmp_path: Path) -> PipelineContext:
    run = Run(repo="acme/arm-control", commit_sha="a" * 40, branch="main")
    bus = EventBus()
    ws = Workspace(
        run_id=run.id, repo=run.repo, commit_sha=run.commit_sha, root=tmp_path
    )
    return PipelineContext(
        run=run,
        workspace=ws,
        bus=bus,
        blackboard=Blackboard(run.id, bus),
        devin=None,  # type: ignore[arg-type]
    )


def oracle_result(scenario_id: str, seed: int, status: str) -> SimpleNamespace:
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


def verify_ctx(tmp_path: Path) -> PipelineContext:
    ctx = make_ctx(tmp_path)
    ctx.run.suite = SuiteStats(total=2, passed=1, failed=1, pass_rate=0.5)
    ctx.scenarios = [
        Scenario(
            run_id=ctx.run.id,
            id="failed",
            index=0,
            seed=11,
            status=ScenarioStatus.FAILED,
        ),
        Scenario(
            run_id=ctx.run.id,
            id="passing",
            index=1,
            seed=22,
            status=ScenarioStatus.PASSED,
        ),
    ]
    return ctx


class NoopReviewer:
    def __init__(self, _ctx: PipelineContext) -> None:
        pass

    async def dispatch(self, **_kwargs: object) -> None:
        return None


class NoopReporter:
    def __init__(self, _ctx: PipelineContext) -> None:
        pass

    async def dispatch(self, **_kwargs: object) -> None:
        return None


# -- the table itself ------------------------------------------------------- #


def test_every_stage_has_an_entry() -> None:
    assert set(TRANSITIONS) == set(Stage)


def test_terminal_stages_have_no_exits() -> None:
    for stage in TERMINAL_STAGES:
        assert TRANSITIONS[stage] == ()
    for stage, targets in TRANSITIONS.items():
        if targets:
            assert stage not in TERMINAL_STAGES


def test_shipping_a_fix_must_pass_through_verify() -> None:
    """No path from FIX to a PR that skips VERIFY. Simulation disposes."""
    assert Stage.REPORT not in TRANSITIONS[Stage.FIX]
    assert Stage.PR_OPENED not in TRANSITIONS[Stage.FIX]
    assert TRANSITIONS[Stage.FIX] == (Stage.VERIFY, Stage.FAILED_UNRESOLVED)
    assert Stage.REPORT in TRANSITIONS[Stage.VERIFY]
    # And VERIFY can send work back for another attempt.
    assert Stage.FIX in TRANSITIONS[Stage.VERIFY]
    # A VERIFY infrastructure crash may use the direct failure transition;
    # red verification results themselves route through REPORT.
    assert Stage.FAILED_UNRESOLVED in TRANSITIONS[Stage.VERIFY]


def test_a_clean_suite_exits_without_agents() -> None:
    assert Stage.PASSED_CLEAN in TRANSITIONS[Stage.RUN_SUITE]
    assert can_transition(Stage.RUN_SUITE, Stage.PASSED_CLEAN)
    assert not can_transition(Stage.RUN_SUITE, Stage.INVESTIGATE)


def test_empty_config_gets_default_axes_and_diverse_parameters(
    tmp_path: Path,
) -> None:
    from simkit import scenarios as scenario_gen

    ctx = make_ctx(tmp_path)
    agent = Agent(run_id=ctx.run.id, role=Role.SCENARIO_DESIGNER)
    axes = Pipeline(ctx)._axes(agent)

    assert len(axes) >= 3
    generated = scenario_gen.generate(ctx.run.id, 1337, 50, axes)
    params = {tuple(sorted(scenario["params"].items())) for scenario in generated}
    assert len(params) > 1


def test_worker_count_uses_context_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIM_WORKERS", "99")
    ctx = make_ctx(tmp_path)
    ctx.sim_workers = 2

    assert Pipeline(ctx)._worker_count() == min(2, os.cpu_count() or 1)


def test_every_stage_can_reach_a_terminal_stage() -> None:
    seen: set[Stage] = set()

    def reaches_terminal(stage: Stage) -> bool:
        if stage in TERMINAL_STAGES:
            return True
        if stage in seen:
            return False
        seen.add(stage)
        return any(reaches_terminal(nxt) for nxt in TRANSITIONS[stage])

    for stage in Stage:
        seen.clear()
        assert reaches_terminal(stage), f"{stage} is a dead end"


# -- advance ---------------------------------------------------------------- #


async def test_advance_emits_stage_changed_in_protocol_shape(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    pipe = Pipeline(ctx)

    await pipe.advance(Stage.RESOLVE_MODEL)

    [event] = ctx.bus.history(ctx.run.id)
    assert event.type is EventType.RUN_STAGE_CHANGED
    assert event.data == {
        "stage": "RESOLVE_MODEL",
        "previous_stage": "TRIGGERED",
    }
    assert ctx.run.stage is Stage.RESOLVE_MODEL


async def test_advance_rejects_illegal_transitions(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    pipe = Pipeline(ctx)

    with pytest.raises(ValueError, match="illegal transition"):
        await pipe.advance(Stage.REPORT)
    assert ctx.run.stage is Stage.TRIGGERED
    assert ctx.bus.history(ctx.run.id) == []


async def test_advance_to_the_current_stage_is_a_no_op(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    pipe = Pipeline(ctx)
    await pipe.advance(Stage.TRIGGERED)
    assert ctx.bus.history(ctx.run.id) == []


@pytest.mark.parametrize(
    ("config", "expected_robot"),
    [
        ({}, {"menagerie": "custom_arm"}),
        ({"robot": {"menagerie": "committed_arm"}}, {"menagerie": "committed_arm"}),
        ({"robot": {"model_path": "robot.xml"}}, {"model_path": "robot.xml"}),
    ],
)
async def test_registry_robot_model_is_only_a_config_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    expected_robot: dict,
) -> None:
    ctx = make_ctx(tmp_path)
    ctx.default_robot_menagerie = "custom_arm"
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ctx.workspace.base.mkdir(parents=True)
    (ctx.workspace.base / "main.py").write_text("")

    async def fake_clone(*_args: object, **_kwargs: object) -> Workspace:
        return ctx.workspace

    async def fake_read_config(_workspace: Workspace) -> dict:
        config.setdefault("control", {"entrypoint": "main.py"})
        return config

    async def fake_set_commit_status(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(workspace_mod, "clone", fake_clone)
    monkeypatch.setattr(workspace_mod, "read_config", fake_read_config)
    monkeypatch.setattr(
        pipeline_mod.github, "set_commit_status", fake_set_commit_status
    )

    assert await Pipeline(ctx).stage_triggered() is Stage.RESOLVE_MODEL
    assert ctx.config["robot"] == expected_robot


# -- run() ------------------------------------------------------------------ #


async def test_infrastructure_failure_lands_in_failed_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    pipe = Pipeline(ctx)

    async def boom() -> Stage:
        raise PipelineError("could not clone")

    async def no_cleanup(ws: Workspace, keep_artifacts: bool = True) -> None:
        return None

    monkeypatch.setattr(pipe, "stage_triggered", boom)
    monkeypatch.setattr(workspace_mod, "cleanup", no_cleanup)

    run = await pipe.run()

    assert run.stage is Stage.FAILED_UNRESOLVED
    assert run.error is not None and "could not clone" in run.error
    assert run.finished_at is not None

    events = ctx.bus.history(run.id)
    types = [e.type for e in events]
    assert types[0] is EventType.RUN_CREATED
    assert types[-1] is EventType.RUN_FINISHED
    error = next(e for e in events if e.type is EventType.ERROR)
    # An error event is infrastructure, never a robot test failure.
    assert error.data == {
        "stage": "TRIGGERED",
        "message": run.error,
        "fatal": True,
    }


async def test_a_clean_suite_short_circuits_the_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    pipe = Pipeline(ctx)
    visited: list[Stage] = []

    def stub(stage: Stage, nxt: Stage):
        async def handler() -> Stage:
            visited.append(stage)
            return nxt

        return handler

    monkeypatch.setattr(
        pipe, "stage_triggered", stub(Stage.TRIGGERED, Stage.RESOLVE_MODEL)
    )
    monkeypatch.setattr(
        pipe, "stage_resolve_model", stub(Stage.RESOLVE_MODEL, Stage.BUILD_HARNESS)
    )
    monkeypatch.setattr(
        pipe, "stage_build_harness", stub(Stage.BUILD_HARNESS, Stage.DESIGN_SCENARIOS)
    )
    monkeypatch.setattr(
        pipe, "stage_design_scenarios", stub(Stage.DESIGN_SCENARIOS, Stage.RUN_SUITE)
    )
    monkeypatch.setattr(
        pipe, "stage_run_suite", stub(Stage.RUN_SUITE, Stage.PASSED_CLEAN)
    )

    async def explode() -> Stage:
        raise AssertionError("a clean suite must not investigate anything")

    monkeypatch.setattr(pipe, "stage_cluster_failures", explode)

    async def no_cleanup(ws: Workspace, keep_artifacts: bool = True) -> None:
        return None

    monkeypatch.setattr(workspace_mod, "cleanup", no_cleanup)

    run = await pipe.run()

    assert run.stage is Stage.PASSED_CLEAN
    assert run.error is None
    assert visited == [
        Stage.TRIGGERED,
        Stage.RESOLVE_MODEL,
        Stage.BUILD_HARNESS,
        Stage.DESIGN_SCENARIOS,
        Stage.RUN_SUITE,
    ]
    stages = [
        e.data["stage"]
        for e in ctx.bus.history(run.id)
        if e.type is EventType.RUN_STAGE_CHANGED
    ]
    assert stages[-1] == "PASSED_CLEAN"


async def test_pr_opened_side_effects_run_before_the_terminal_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR_OPENED is terminal, so the PR must be opened on the way in."""
    ctx = make_ctx(tmp_path)
    ctx.run.stage = Stage.REPORT
    pipe = Pipeline(ctx)
    opened: list[str] = []

    async def stage_report() -> Stage:
        return Stage.PR_OPENED

    async def stage_pr_opened() -> Stage:
        opened.append("pr")
        ctx.run.pull_request_url = "https://github.com/acme/arm-control/pull/1"
        return Stage.PR_OPENED

    async def no_cleanup(ws: Workspace, keep_artifacts: bool = True) -> None:
        return None

    monkeypatch.setattr(pipe, "stage_report", stage_report)
    monkeypatch.setattr(pipe, "stage_pr_opened", stage_pr_opened)
    monkeypatch.setattr(workspace_mod, "cleanup", no_cleanup)

    run = await pipe.run()

    assert opened == ["pr"]
    assert run.stage is Stage.PR_OPENED
    assert run.pull_request_url is not None


def test_module_exposes_a_headless_entrypoint() -> None:
    assert callable(pipeline_mod.run_headless)
    assert callable(pipeline_mod.main)


@pytest.mark.parametrize(
    ("after_statuses", "conflicts", "expected_error"),
    [
        (["failed", "passed"], [], "1 scenarios still failing"),
        (["passed", "failed"], [], "newly broken seeds: 22"),
        (
            ["passed", "passed"],
            [
                PatchConflict(
                    worktree="fix-cls-1",
                    branch="test",
                    sha="a" * 40,
                    files=("control.py",),
                    blocked_by=("fix-cls-0",),
                )
            ],
            "unresolved patch conflicts",
        ),
    ],
)
async def test_unsuccessful_verify_reports_failure_without_opening_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_statuses: list[str],
    conflicts: list[PatchConflict],
    expected_error: str,
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    ctx = verify_ctx(tmp_path)
    pipe = Pipeline(ctx)
    pipe._before_results = [
        oracle_result("failed", 11, "failed"),
        oracle_result("passing", 22, "passed"),
    ]
    pipe._conflicts = conflicts
    after_results = [
        oracle_result("failed", 11, after_statuses[0]),
        oracle_result("passing", 22, after_statuses[1]),
    ]

    async def execute_suite(
        _scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return after_results

    async def diff(*_args: object, **_kwargs: object) -> str:
        return ""

    comments: list[str] = []
    statuses: list[tuple[str, str]] = []

    async def comment(_repo: str, _sha: str, body: str) -> None:
        comments.append(body)

    async def status(
        _repo: str,
        _sha: str,
        state: str,
        description: str,
        target_url: str | None = None,
    ) -> None:
        del target_url
        statuses.append((state, description))

    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "diff", diff)
    monkeypatch.setattr(pipeline_mod, "ReviewerAgent", NoopReviewer)
    monkeypatch.setattr(pipeline_mod, "ReporterAgent", NoopReporter)
    monkeypatch.setattr(pipeline_mod.github, "comment_on_commit", comment)
    monkeypatch.setattr(pipeline_mod.github, "set_commit_status", status)

    ctx.run.stage = Stage.VERIFY
    pipe.ctx.max_fix_iterations = 1
    nxt = await pipe.stage_verify()
    assert nxt is Stage.REPORT
    assert ctx.run.error is not None and expected_error in ctx.run.error

    await pipe.advance(Stage.REPORT)
    terminal = await pipe.stage_report()
    assert terminal is Stage.FAILED_UNRESOLVED
    await pipe.advance(terminal)
    assert ctx.run.stage is Stage.FAILED_UNRESOLVED
    assert comments
    assert statuses and statuses[-1][0] == "failure"


async def test_green_verify_reaches_pr_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    ctx = verify_ctx(tmp_path)
    pipe = Pipeline(ctx)
    pipe._before_results = [
        oracle_result("failed", 11, "failed"),
        oracle_result("passing", 22, "passed"),
    ]
    after_results = [
        oracle_result("failed", 11, "passed"),
        oracle_result("passing", 22, "passed"),
    ]

    async def execute_suite(
        _scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[SimpleNamespace]:
        del repo_dir
        return after_results

    async def diff(*_args: object, **_kwargs: object) -> str:
        return ""

    async def push(*_args: object, **_kwargs: object) -> str:
        return "b" * 40

    async def open_pr(*_args: object, **_kwargs: object) -> str:
        return "https://github.com/acme/arm-control/pull/1"

    async def status(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(pipe, "_execute_suite", execute_suite)
    monkeypatch.setattr(pipeline_mod.workspace_mod, "diff", diff)
    monkeypatch.setattr(pipeline_mod, "ReviewerAgent", NoopReviewer)
    monkeypatch.setattr(pipeline_mod, "ReporterAgent", NoopReporter)
    monkeypatch.setattr(pipeline_mod.github, "push_branch", push)
    monkeypatch.setattr(pipeline_mod.github, "open_pull_request", open_pr)
    monkeypatch.setattr(pipeline_mod.github, "set_commit_status", status)

    ctx.run.stage = Stage.VERIFY
    assert await pipe.stage_verify() is Stage.REPORT
    await pipe.advance(Stage.REPORT)
    assert await pipe.stage_report() is Stage.PR_OPENED
    await pipe.stage_pr_opened()
    await pipe.advance(Stage.PR_OPENED)
    assert ctx.run.stage is Stage.PR_OPENED
    assert ctx.run.pull_request_url == "https://github.com/acme/arm-control/pull/1"


async def test_resolve_model_cache_hit_skips_modeler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ctx = make_ctx(tmp_path / "workspace")
    ctx.config = {"robot": {}}
    pipe = Pipeline(ctx)
    cached = resolver_module.Resolution(
        found=True,
        source="repo",
        name="cached_arm",
        model_path=str(tmp_path / "cached.xml"),
        dof=2,
        confidence=0.98,
        provenance="cache source",
        processing_steps=["MJCF validation"],
        cache_hit=True,
    )

    async def fail_modeler(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Modeler must not run on a cache hit")

    monkeypatch.setattr(resolver_module, "resolve", lambda *_args, **_kwargs: cached)
    monkeypatch.setattr(pipeline_mod, "ModelerAgent", fail_modeler)

    assert await pipe.stage_resolve_model() is Stage.BUILD_HARNESS
    assert ctx.run.robot_model is not None
    assert ctx.run.robot_model.cache_hit is True
    assert ctx.run.robot_model.provenance == "cache source"


# -- stage_build_harness() ---------------------------------------------------- #


def _harness_stub_builder(output: dict[str, object]):
    class StubBuilder:
        def __init__(self, ctx: PipelineContext) -> None:
            self.ctx = ctx
            self.output: dict[str, object] = {}

        async def dispatch(self, **_kwargs: object) -> Agent:
            self.output = dict(output)
            return Agent(
                run_id=self.ctx.run.id,
                role=Role.HARNESS_BUILDER,
                title="Test Infrastructure Engineer",
                task="harness",
            )

    return StubBuilder


def _harness_ctx(tmp_path: Path) -> PipelineContext:
    ctx = make_ctx(tmp_path)
    ctx.run.robot_model = pipeline_mod.RobotModel(
        source="repo", model_path=str(tmp_path / "model.xml")
    )
    return ctx


async def test_build_harness_materializes_returned_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent works remotely: its harness arrives as source, not a file."""
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ctx = _harness_ctx(tmp_path)
    pipe = Pipeline(ctx)
    code = "def run_episode(model, data, params):\n    return None\n"
    monkeypatch.setattr(
        pipeline_mod,
        "HarnessBuilderAgent",
        _harness_stub_builder({"harness_path": "harness.py", "harness_code": code}),
    )

    from simkit import runner

    def fake_run_scenario(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(status="passed", error=None)

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)

    assert await pipe.stage_build_harness() is Stage.DESIGN_SCENARIOS
    written = tmp_path / "artifacts" / ctx.run.id / "harness.py"
    assert written.read_text(encoding="utf-8") == code


async def test_build_harness_without_code_is_a_pipeline_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    ctx = _harness_ctx(tmp_path)
    pipe = Pipeline(ctx)
    monkeypatch.setattr(
        pipeline_mod,
        "HarnessBuilderAgent",
        _harness_stub_builder({"harness_path": "harness.py"}),
    )

    with pytest.raises(PipelineError, match="no harness_code"):
        await pipe.stage_build_harness()
