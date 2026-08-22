"""Every role prompt renders fully, with no unfilled placeholders left."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator.pipeline import PipelineContext
from orchestrator.roles.base import MISSING, PROMPTS_DIR, RoleAgent
from orchestrator.roles.fixer import FixerAgent
from orchestrator.roles.harness_builder import HarnessBuilderAgent
from orchestrator.roles.investigator import InvestigatorAgent
from orchestrator.roles.modeler import ModelerAgent
from orchestrator.roles.reporter import ReporterAgent
from orchestrator.roles.reviewer import ReviewerAgent
from orchestrator.roles.scenario_designer import ScenarioDesignerAgent

ROLE_CLASSES: list[type[RoleAgent]] = [
    ModelerAgent,
    HarnessBuilderAgent,
    ScenarioDesignerAgent,
    InvestigatorAgent,
    FixerAgent,
    ReviewerAgent,
    ReporterAgent,
]


def test_no_prompt_still_carries_a_build_todo() -> None:
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        assert "TODO(build)" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize("cls", ROLE_CLASSES, ids=lambda c: c.role.value)
def test_prompt_renders_without_leftover_placeholders(
    cls: type[RoleAgent], ctx: PipelineContext
) -> None:
    prompt = cls(ctx).render_prompt()
    assert "{{" not in prompt
    assert MISSING not in prompt, "a template placeholder has no supplying role"


@pytest.mark.parametrize("cls", ROLE_CLASSES, ids=lambda c: c.role.value)
def test_prompt_carries_the_shared_header_and_context(
    cls: type[RoleAgent], ctx: PipelineContext
) -> None:
    prompt = cls(ctx).render_prompt()
    assert ctx.run.repo in prompt
    assert ctx.run.commit_sha in prompt
    # The blackboard context the relay policy allows this role to see.
    assert f"context for {cls.role.value}" in prompt


def test_scenario_designer_prompt_states_the_axis_cap(ctx: PipelineContext) -> None:
    prompt = ScenarioDesignerAgent(ctx).render_prompt()
    assert str(ScenarioDesignerAgent.max_axes) in prompt


def test_investigator_prompt_includes_trace_paths(ctx: PipelineContext) -> None:
    prompt = InvestigatorAgent(ctx).render_prompt(
        trace_paths=["artifacts/run/traces/seed-4471.json"]
    )
    assert "seed-4471.json" in prompt


def test_reviewer_prompt_includes_the_actual_diff(ctx: PipelineContext) -> None:
    prompt = ReviewerAgent(ctx).render_prompt(diff="--- a/src/controller.py")
    assert "--- a/src/controller.py" in prompt


def test_fixer_prompt_carries_failed_theories_and_reviewer_notes(
    ctx: PipelineContext,
) -> None:
    prompt = FixerAgent(ctx).render_prompt(
        failed_theories=["Timer was not the cause"],
        reviewer_notes="Patch broke seed 12",
    )
    assert "Timer was not the cause" in prompt
    assert "Patch broke seed 12" in prompt


def test_template_is_shared_plus_role(ctx: PipelineContext) -> None:
    shared = (PROMPTS_DIR / "_shared.md").read_text(encoding="utf-8").strip()
    template = ModelerAgent(ctx).load_template()
    assert template.startswith(shared[:80])
    assert "menagerie_index" in Path(PROMPTS_DIR / "modeler.md").read_text(
        encoding="utf-8"
    )
