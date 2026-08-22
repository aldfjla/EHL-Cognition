"""Hardware Engineer — resolve or generate the robot's physical model.

Stage: ``RESOLVE_MODEL``.

Runs only when `simkit.models.resolver` fails to match the repo's robot against
the MuJoCo Menagerie. Reads the repo's drivers, URDFs and calibration constants
to identify the hardware, then either names the Menagerie model the automatic
search missed or synthesizes an MJCF from the kinematics.

Its output is validated by loading the model in MuJoCo before acceptance — an
unloadable model would fail every downstream stage with a misleading error.

Inputs:  the checkout, the resolver's miss report.
Outputs: a `RobotModel` the suite can simulate.

Findings written
----------------
`observation` — how the robot was identified.
`constraint` — any property the agent had to guess (masses, inertias), so a
later Investigator knows a failure might be the model's fault, not the code's.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, Role


class ModelerAgent(RoleAgent):
    """Hardware Engineer."""

    role = Role.MODELER
    prompt_file = "modeler.md"
    display_name = "Hardware Engineer"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/modeler.md``."""
        raise NotImplementedError
        # TODO(build): pass resolver_report, model_out_dir, and the Menagerie index summary.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
