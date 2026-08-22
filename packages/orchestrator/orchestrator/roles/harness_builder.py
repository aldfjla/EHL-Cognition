"""Test Infrastructure — bind the pushed control code to the simulator.

Stage: ``BUILD_HARNESS``.

Writes the adapter that lets the customer's unmodified entrypoint drive MuJoCo
actuators instead of a hardware driver. The highest-leverage role in the system:
everything downstream tests whatever this agent built, so a subtly wrong harness
produces a run's worth of confident, wrong conclusions.

Acceptance is therefore not the agent's word — the pipeline runs one smoke
scenario and checks the robot actually moved.

Inputs:  the checkout, `control.entrypoint`, the resolved model.
Outputs: a harness module exposing `run_episode`.

Findings written
----------------
`constraint` — assumptions the customer's code makes that the harness had to
honour (start pose, units, control rate). Every later agent must respect these.
`observation` — the shims that were faked, so a fix touching a driver boundary
is recognisable.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, Role


class HarnessBuilderAgent(RoleAgent):
    """Test Infrastructure Engineer."""

    role = Role.HARNESS_BUILDER
    prompt_file = "harness_builder.md"
    display_name = "Test Infrastructure Engineer"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/harness_builder.md``."""
        raise NotImplementedError
        # TODO(build): pass entrypoint, interface, rate_hz, model_path, harness_out_path.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
