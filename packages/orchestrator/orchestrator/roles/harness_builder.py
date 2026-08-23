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
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class HarnessBuilderAgent(RoleAgent):
    """Test Infrastructure Engineer."""

    role = Role.HARNESS_BUILDER
    prompt_file = "harness_builder.md"
    display_name = "Test Infrastructure Engineer"
    required_keys = ("harness_path", "harness_code", "interface_notes")

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/harness_builder.md``."""
        control = (self.ctx.config.get("control") or {}) if self.ctx.config else {}
        model = self.ctx.run.robot_model
        return {
            "entrypoint": kwargs.get("entrypoint") or control.get("entrypoint", ""),
            "interface": kwargs.get("interface")
            or control.get("interface", "joint_position"),
            "rate_hz": kwargs.get("rate_hz") or control.get("rate_hz", 100),
            "model_path": kwargs.get("model_path")
            or (model.model_path if model else ""),
            "harness_out_path": kwargs.get("harness_out_path")
            or (self.ctx.workspace.base / "robotci_harness.py"),
        }

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """The deliverable is the module source, not a pointer to it.

        Agents have returned the prompt's example placeholder or an attachment
        URL in ``harness_code``; anything without a ``run_episode`` definition
        cannot pass the smoke test, so reject it here and let the retry
        reminder ask for the real source.
        """
        cleaned = super().validate_output(output)
        code = str(cleaned.get("harness_code") or "")
        if "def run_episode" not in code:
            raise ValueError(
                "harness_code must be the complete harness module source and "
                "define `run_episode(model, data, params)`; placeholders, "
                "paths, and attachments are not readable by the orchestrator"
            )
        return cleaned

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        harness_path = str(output.get("harness_path", ""))
        findings = [
            self.finding(
                agent,
                FindingKind.OBSERVATION,
                f"Harness binds the pushed entrypoint at {harness_path}",
                detail="\n".join(
                    [
                        str(output.get("interface_notes", "")),
                        *(f"Shim: {shim}" for shim in output.get("shims") or []),
                    ]
                ).strip(),
                confidence=confidence,
                files=[harness_path] if harness_path else None,
            )
        ]
        # Constraints are broadcast: an agent that violates one of these makes
        # every downstream result wrong.
        for constraint in output.get("constraints") or []:
            findings.append(
                self.finding(
                    agent,
                    FindingKind.CONSTRAINT,
                    str(constraint),
                    detail="Assumption the pushed control code makes; the "
                    "harness honours it and so must every patch.",
                    confidence=confidence,
                )
            )
        return findings
