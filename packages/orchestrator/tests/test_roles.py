"""Role validation, finding conversion, dispatch and the offline mock."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeBlackboard, FakeBus
from orchestrator.pipeline import PipelineContext
from orchestrator.roles.base import CANNED_OUTPUT, MockRoleAgent
from orchestrator.roles.fixer import FixerAgent, agent_slug
from orchestrator.roles.harness_builder import HarnessBuilderAgent
from orchestrator.roles.investigator import InvestigatorAgent
from orchestrator.roles.modeler import ModelerAgent
from orchestrator.roles.reporter import ReporterAgent
from orchestrator.roles.reviewer import ReviewerAgent
from orchestrator.roles.scenario_designer import ScenarioDesignerAgent
from orchestrator.schemas import (
    Agent,
    AgentStatus,
    Cluster,
    Finding,
    FindingKind,
    FindingStatus,
    Role,
    Speaker,
    Verdict,
)


def agent_for(ctx: PipelineContext, role: Role, **kwargs: Any) -> Agent:
    return Agent(run_id=ctx.run.id, role=role, **kwargs)


# -- validation ------------------------------------------------------------- #


def test_free_text_is_rejected(ctx: PipelineContext) -> None:
    role = InvestigatorAgent(ctx)
    with pytest.raises(ValueError, match="empty"):
        role.validate_output({})
    with pytest.raises(ValueError, match="missing required keys"):
        role.validate_output({"root_cause": "the timer"})


def test_confidence_is_coerced_and_clamped(ctx: PipelineContext) -> None:
    role = InvestigatorAgent(ctx)
    output = role.validate_output(
        {"root_cause": "timer", "evidence": "seed 4471", "confidence": "1.7"}
    )
    assert output["confidence"] == 1.0
    with pytest.raises(ValueError, match="confidence must be a number"):
        role.validate_output(
            {"root_cause": "t", "evidence": "e", "confidence": "very sure"}
        )


def test_scenario_designer_rejects_too_many_axes(ctx: PipelineContext) -> None:
    role = ScenarioDesignerAgent(ctx)
    axes = {f"a{i}": {"low": 0, "high": 1} for i in range(5)}
    with pytest.raises(ValueError, match="too many"):
        role.validate_output({"axes": axes})
    with pytest.raises(ValueError, match="numeric"):
        role.validate_output({"axes": {"mass": {"low": 0.1}}})
    assert role.validate_output({"axes": {"mass": {"low": 0.1, "high": 0.8}}})


def test_reviewer_and_reporter_verdicts_are_constrained(ctx: PipelineContext) -> None:
    with pytest.raises(ValueError):
        ReviewerAgent(ctx).validate_output({"verdict": "looks fine"})
    assert ReviewerAgent(ctx).validate_output({"verdict": "SHIP"})["verdict"] == "ship"

    with pytest.raises(ValueError):
        ReporterAgent(ctx).validate_output(
            {"verdict": "ship", "title": "t", "summary": "s"}
        )
    assert (
        ReporterAgent(ctx).validate_output(
            {"verdict": "Fixed", "title": "t", "summary": "s"}
        )["verdict"]
        == "fixed"
    )


def test_harness_builder_rejects_placeholder_harness_code(
    ctx: PipelineContext,
) -> None:
    role = HarnessBuilderAgent(ctx)
    base = {
        "harness_path": "/tmp/harness.py",
        "interface_notes": "notes",
    }
    with pytest.raises(ValueError, match="run_episode"):
        role.validate_output(
            {
                **base,
                "harness_code": "# the complete harness module source, JSON-escaped",
            }
        )
    with pytest.raises(ValueError, match="run_episode"):
        role.validate_output(
            {**base, "harness_code": "https://app.devin.ai/attachments/abc/harness.py"}
        )
    accepted = role.validate_output(
        {
            **base,
            "harness_code": "import mujoco\n\ndef run_episode(model, data, params):\n    ...",
        }
    )
    assert "def run_episode" in accepted["harness_code"]


# -- findings --------------------------------------------------------------- #


def test_modeler_findings_split_match_from_assumptions(ctx: PipelineContext) -> None:
    role = ModelerAgent(ctx)
    agent = agent_for(ctx, Role.MODELER)
    findings = role.to_findings(agent, CANNED_OUTPUT[Role.MODELER])
    kinds = [f.kind for f in findings]
    assert kinds[0] is FindingKind.OBSERVATION
    assert FindingKind.CONSTRAINT in kinds
    assert all(f.author_role is Speaker.MODELER for f in findings)


def test_harness_findings_record_constraints(ctx: PipelineContext) -> None:
    findings = HarnessBuilderAgent(ctx).to_findings(
        agent_for(ctx, Role.HARNESS_BUILDER), CANNED_OUTPUT[Role.HARNESS_BUILDER]
    )
    assert any(f.kind is FindingKind.CONSTRAINT for f in findings)


def test_investigator_root_cause_confidence_is_capped_when_not_reproduced(
    ctx: PipelineContext,
) -> None:
    output = dict(CANNED_OUTPUT[Role.INVESTIGATOR], reproduced=False, confidence=0.9)
    findings = InvestigatorAgent(ctx).to_findings(
        agent_for(ctx, Role.INVESTIGATOR, cluster_id="cls-1"), output
    )
    root = next(f for f in findings if f.kind is FindingKind.ROOT_CAUSE)
    assert root.confidence <= 0.3
    assert root.cluster_id == "cls-1"


def test_fixer_patch_finding_and_worktree_slug(ctx: PipelineContext) -> None:
    findings = FixerAgent(ctx).to_findings(
        agent_for(ctx, Role.FIXER, cluster_id="cls-7"), CANNED_OUTPUT[Role.FIXER]
    )
    patch = next(f for f in findings if f.kind is FindingKind.PATCH)
    assert "src/controller.py" in patch.files
    assert agent_slug({"cluster_id": "cls-7"}) == "cls-7"
    assert agent_slug({}) == "unclustered"


def test_fixer_rejects_a_claimed_fix_without_a_diff(ctx: PipelineContext) -> None:
    role = FixerAgent(ctx)
    output = dict(CANNED_OUTPUT[Role.FIXER], patched=True)
    output.pop("patch", None)
    with pytest.raises(ValueError, match="patch"):
        role.validate_output(output)
    output["patch"] = "diff --git a/x b/x\n"
    assert role.validate_output(output)["patch"].startswith("diff --git")


def test_fixer_template_vars_accept_a_finding_root_cause(
    ctx: PipelineContext,
) -> None:
    """The pipeline hands the Fixer the confirmed root-cause Finding itself."""
    cause = Finding(
        run_id=ctx.run.id,
        author_agent_id="agt-1",
        author_role=Speaker.INVESTIGATOR,
        kind=FindingKind.ROOT_CAUSE,
        summary="wrist target sends radians into a degree field",
        detail="src/sock_pick.py:33 converts twice",
        files=["src/sock_pick.py"],
    )
    variables = FixerAgent(ctx).template_vars(
        root_cause=cause, worktree="/tmp/wt", cluster_id="cls-7"
    )
    assert variables["root_cause"] == "src/sock_pick.py:33 converts twice"
    assert variables["files"] == ["src/sock_pick.py"]


def test_fixer_confidence_capped_when_not_patched(ctx: PipelineContext) -> None:
    output = dict(CANNED_OUTPUT[Role.FIXER], patched=False, confidence=0.9)
    patch = next(
        f
        for f in FixerAgent(ctx).to_findings(agent_for(ctx, Role.FIXER), output)
        if f.kind is FindingKind.PATCH
    )
    assert patch.confidence <= 0.2


def test_reporter_writes_no_findings_but_builds_a_report(
    ctx: PipelineContext,
) -> None:
    role = ReporterAgent(ctx)
    ctx.clusters = [Cluster(run_id=ctx.run.id, id="cls-1", label="gripper", size=4)]
    output = {
        "verdict": "fixed",
        "title": "Close on contact",
        "summary": "Fixed the early close.",
        "incidents": [
            {
                "cluster_id": "cls-1",
                "title": "Gripper closes early",
                "root_cause": "fixed timer",
                "resolution": "gate on contact",
                "files_changed": ["src/controller.py"],
                "status": "fixed",
            }
        ],
    }
    assert role.to_findings(agent_for(ctx, Role.REPORTER), output) == []
    report = role.to_report(
        output,
        video_pairs={
            "cls-1": {
                "before": "https://example.com/before.mp4",
                "after": "https://example.com/after.mp4",
            }
        },
    )
    assert report.verdict is Verdict.FIXED
    incident = report.incidents[0]
    assert incident.affected_scenarios == 4
    assert str(incident.before_video).startswith("https://")


async def test_reviewer_adjudicates_the_board(
    ctx: PipelineContext, blackboard: FakeBlackboard
) -> None:
    kept = Finding(
        run_id=ctx.run.id,
        author_agent_id="agt-1",
        author_role=Speaker.INVESTIGATOR,
        kind=FindingKind.ROOT_CAUSE,
        summary="fixed timer",
    )
    dropped = Finding(
        run_id=ctx.run.id,
        author_agent_id="agt-2",
        author_role=Speaker.INVESTIGATOR,
        kind=FindingKind.ROOT_CAUSE,
        summary="model mass wrong",
    )
    await blackboard.write(kept)
    await blackboard.write(dropped)

    role = ReviewerAgent(ctx)
    await role.adjudicate(
        {
            "verdict": "ship",
            "accepted_findings": [kept.id],
            "rejected_findings": [dropped.id],
        }
    )
    assert blackboard.confirmed == [kept.id]
    assert blackboard.refuted and blackboard.refuted[0][0] == dropped.id


# -- dispatch --------------------------------------------------------------- #


async def test_mock_dispatch_emits_the_same_events_as_a_real_one(
    ctx: PipelineContext, bus: FakeBus, blackboard: FakeBlackboard
) -> None:
    role = MockRoleAgent(ctx, Role.INVESTIGATOR)
    agent = await role.dispatch(cluster_id="cls-1", task="find it")

    assert agent.status is AgentStatus.SUCCEEDED
    assert agent.session_id == "mock"
    types = bus.types()
    assert types[0] == "agent.created"
    assert "agent.activity" in types
    assert types[-1] == "agent.status_changed"
    assert blackboard.findings and agent.finding_ids
    assert role.output["root_cause"]


async def test_mock_dispatch_can_return_supplied_findings(
    ctx: PipelineContext, blackboard: FakeBlackboard
) -> None:
    canned = Finding(
        run_id=ctx.run.id,
        author_agent_id="agt-x",
        author_role=Speaker.MODELER,
        kind=FindingKind.CONSTRAINT,
        summary="joint 4 has a hard stop",
        status=FindingStatus.CONFIRMED,
    )
    role = MockRoleAgent(ctx, Role.MODELER, findings=[canned])
    await role.dispatch()
    assert blackboard.findings == [canned]


async def test_real_dispatch_retries_once_on_bad_output(
    ctx: PipelineContext, bus: FakeBus, blackboard: FakeBlackboard
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.outputs = [
                {"evidence": "only prose"},
                CANNED_OUTPUT[Role.INVESTIGATOR],
            ]
            self.messages: list[str] = []

        async def create_session(self, prompt: str, **kwargs: Any) -> Any:
            self.prompt = prompt
            return type("H", (), {"session_id": "s-1", "url": "u", "status": "working"})

        async def wait_until_done(self, session_id: str, **kwargs: Any) -> dict:
            on_activity = kwargs.get("on_activity")
            if on_activity is not None:
                await on_activity("reading controller.py")
            return {"status": "finished", "messages": []}

        async def send_message(self, session_id: str, message: str) -> None:
            self.messages.append(message)

        async def structured_output(self, session_id: str) -> dict:
            return self.outputs.pop(0)

    client = FakeClient()
    ctx.devin = client  # type: ignore[assignment]
    role = InvestigatorAgent(ctx)
    agent = await role.dispatch(cluster_label="gripper closes early")

    assert agent.status is AgentStatus.SUCCEEDED
    assert client.messages, "a rejected output must draw exactly one reminder"
    assert len(client.messages) == 1
    assert agent.last_activity == "reading controller.py"
    assert any(f.kind is FindingKind.ROOT_CAUSE for f in blackboard.findings)


async def test_dispatch_marks_the_agent_failed_when_the_session_fails(
    ctx: PipelineContext,
) -> None:
    class BrokenClient:
        async def create_session(self, prompt: str, **kwargs: Any) -> Any:
            return type("H", (), {"session_id": "s-1", "url": "u", "status": "working"})

        async def wait_until_done(self, session_id: str, **kwargs: Any) -> dict:
            raise RuntimeError("devin exploded")

    ctx.devin = BrokenClient()  # type: ignore[assignment]
    role = InvestigatorAgent(ctx)
    with pytest.raises(RuntimeError, match="devin exploded"):
        await role.dispatch()
    assert role.session is not None
    assert role.session.agent.status is AgentStatus.FAILED
