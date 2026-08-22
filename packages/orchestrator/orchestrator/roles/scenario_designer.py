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
from orchestrator.schemas import Agent, Finding, Role


class ScenarioDesignerAgent(RoleAgent):
    """QA Lead."""

    role = Role.SCENARIO_DESIGNER
    prompt_file = "scenario_designer.md"
    display_name = "QA Lead"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/scenario_designer.md``."""
        raise NotImplementedError
        # TODO(build): pass task_description, success_criteria, suite_size.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
