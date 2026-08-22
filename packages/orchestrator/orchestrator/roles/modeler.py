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
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class ModelerAgent(RoleAgent):
    """Hardware Engineer."""

    role = Role.MODELER
    prompt_file = "modeler.md"
    display_name = "Hardware Engineer"
    required_keys = ("source", "model_path", "reasoning")

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/modeler.md``."""
        robot = (self.ctx.config.get("robot") or {}) if self.ctx.config else {}
        return {
            "resolver_report": kwargs.get("resolver_report")
            or "no confident Menagerie match",
            "model_out_dir": kwargs.get("model_out_dir")
            or (self.ctx.workspace.worktree("model")),
            "menagerie_index": kwargs.get("menagerie_index") or "(not supplied)",
            "robot_hints": robot,
        }

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        name = output.get("name") or output.get("model_path")
        findings = [
            self.finding(
                agent,
                FindingKind.OBSERVATION,
                f"Robot identified as {name} ({output.get('source')})",
                detail=str(output.get("reasoning", "")),
                confidence=confidence,
                files=[str(output.get("model_path", ""))],
            )
        ]
        # Every guessed physical quantity is a constraint: a later
        # Investigator has to know a failure might be the model's fault.
        for assumption in output.get("assumptions") or []:
            findings.append(
                self.finding(
                    agent,
                    FindingKind.CONSTRAINT,
                    str(assumption),
                    detail=f"Guessed while building {name}.",
                    confidence=confidence,
                )
            )
        return findings
