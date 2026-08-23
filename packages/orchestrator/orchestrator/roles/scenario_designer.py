"""QA Lead — choose what the world matrix varies and over what ranges.

Stage: ``DESIGN_SCENARIOS``.

Decides the randomization *axes*, not the samples. Concrete worlds are sampled
deterministically by `simkit.scenarios` from a seed, so every failure the suite
finds is exactly reproducible for the Investigator.

The agent reads the controller for hardcoded constants — timeouts, gains, grip
widths — because those are where the behavioural boundaries actually sit, and a
matrix that straddles them finds real bugs instead of trivial ones.

Inputs:  the checkout, the task definition and success criteria.
Outputs: axis ranges consumed by `simkit.scenarios.generate`.

Findings written
----------------
`observation` — boundaries discovered in the code, with file:line. These are
what the Investigator reaches for first when a cluster correlates with one axis.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class ScenarioDesignerAgent(RoleAgent):
    """QA Lead."""

    role = Role.SCENARIO_DESIGNER
    prompt_file = "scenario_designer.md"
    display_name = "QA Lead"
    fresh_session = True
    required_keys = ("axes",)

    #: Beyond this, SUITE_SIZE samples cover no axis densely enough to cluster.
    max_axes = 4

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/scenario_designer.md``."""
        task = (self.ctx.config.get("task") or {}) if self.ctx.config else {}
        return {
            "task_description": kwargs.get("task_description")
            or task.get("description")
            or task.get("name")
            or "inferred from the repo",
            "success_criteria": kwargs.get("success_criteria") or task.get("success"),
            "suite_size": kwargs.get("suite_size", 50),
            "max_axes": self.max_axes,
        }

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """``axes`` must be a mapping of named ranges, and not too many."""
        cleaned = super().validate_output(output)
        axes = cleaned.get("axes")
        if not isinstance(axes, dict) or not axes:
            raise ValueError("axes must be a non-empty object of named ranges")
        for name, spec in axes.items():
            if not isinstance(spec, dict) or "low" not in spec or "high" not in spec:
                raise ValueError(f"axis {name!r} needs numeric 'low' and 'high'")
        if len(axes) > self.max_axes:
            raise ValueError(
                f"{len(axes)} axes is too many; keep at most {self.max_axes} so "
                "the suite samples each one densely enough to cluster"
            )
        return cleaned

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        axes = output.get("axes") or {}
        findings = [
            self.finding(
                agent,
                FindingKind.OBSERVATION,
                "Randomization axes: " + ", ".join(sorted(axes)),
                detail="\n".join(
                    f"- {name}: {spec.get('low')}..{spec.get('high')} "
                    f"({spec.get('why', 'no rationale given')})"
                    for name, spec in sorted(axes.items())
                ),
                confidence=confidence,
            )
        ]
        # Boundaries read out of the controller are what the Investigator
        # reaches for first when a cluster correlates with one axis.
        notes = str(output.get("notes") or "").strip()
        if notes:
            findings.append(
                self.finding(
                    agent,
                    FindingKind.OBSERVATION,
                    notes.splitlines()[0][:200],
                    detail=notes,
                    confidence=confidence,
                )
            )
        return findings
