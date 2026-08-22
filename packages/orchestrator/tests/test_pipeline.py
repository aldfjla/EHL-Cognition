"""The stage machine: legal transitions, event emission, failure handling."""

from __future__ import annotations

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
from orchestrator.schemas import TERMINAL_STAGES, EventType, Run, Scenario, Stage
from orchestrator.workspace import Workspace
from simkit import scoring


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


def test_a_clean_suite_exits_without_agents() -> None:
    assert Stage.PASSED_CLEAN in TRANSITIONS[Stage.RUN_SUITE]
    assert can_transition(Stage.RUN_SUITE, Stage.PASSED_CLEAN)
    assert not can_transition(Stage.RUN_SUITE, Stage.INVESTIGATE)


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


def test_apply_result_translates_real_simkit_scoring_output() -> None:
    """The orchestrator publishes simkit's measured value as contract value."""
    raw = SimpleNamespace(
        sim_time_s=1.0,
        trace={"n": 1, "contact_force": [7.5]},
    )
    outcomes, diagnosis = scoring.evaluate(
        raw,
        [
            {"id": "no_collision", "max_force_n": 10.0},
            {"id": "unknown_criterion"},
        ],
    )
    scenario = Scenario(
        run_id="run-test",
        id="scenario-test",
        index=0,
        seed=1,
    )
    result = SimpleNamespace(
        status="failed",
        duration_s=0.1,
        sim_time_s=1.0,
        diagnosis=diagnosis,
        video_path=None,
        trace_path=None,
        error=None,
        worker_id="w0",
        criteria=outcomes,
    )

    pipeline_mod.Pipeline._apply_result(scenario, result)

    assert scenario.criteria[0].value == 7.5
    assert scenario.criteria[0].threshold == 10.0
    assert scenario.criteria[1].value is None
    assert "unknown criterion" in (scenario.diagnosis or "")
