"""Fixer — patch one confirmed root cause and self-verify.

Stage: ``FIX``.

Fanned out one per confirmed root cause, each in its own git worktree so two
agents cannot corrupt each other's diffs. Self-verifies cheaply by re-running
only its own cluster's seeds, then samples previously-passing seeds to catch the
obvious regressions before the expensive full-suite gate at VERIFY.

Bounded by `MAX_AGENT_ITERATIONS`. An agent that exhausts its budget is failed
out rather than left running — an unresolved incident in the report is a better
outcome than an unbounded spend.

Inputs:  a confirmed `root_cause` finding, the team's constraints, a worktree.
Outputs: a patch, plus its own verification evidence.

Findings written
----------------
`patch` — the diff and the reasoning connecting it to the cause.
`verification` — which seeds passed after the change, and what was sampled for
regressions. The Reviewer weighs this against the full-suite result.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class FixerAgent(RoleAgent):
    """Fix Engineer."""

    role = Role.FIXER
    prompt_file = "fixer.md"
    display_name = "Fix Engineer"
    required_keys = ("diff_summary",)

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/fixer.md``."""
        cause = kwargs.get("root_cause")
        cause_text = cause.detail or cause.summary if cause is not None else None
        files = kwargs.get("files") or (list(cause.files) if cause is not None else [])
        return {
            "root_cause": cause_text or "(none supplied)",
            "files": files,
            "constraints": [f.summary for f in self.ctx.blackboard.constraints()],
            "worktree": kwargs.get("worktree")
            or self.ctx.workspace.worktree(f"fix-{agent_slug(kwargs)}"),
            "scenario_seeds": kwargs.get("scenario_seeds") or [],
            "harness_path": kwargs.get("harness_path", ""),
            "iteration": kwargs.get("iteration", 1),
            "max_iterations": kwargs.get("max_iterations", 3),
            # Handing over the dead ends stops the fixer re-deriving them.
            "failed_theories": kwargs.get("failed_theories") or [],
            "reviewer_notes": kwargs.get("reviewer_notes"),
        }

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """A claimed fix must carry the diff the orchestrator can apply."""
        cleaned = super().validate_output(output)
        if cleaned.get("patched") and not str(cleaned.get("patch") or "").strip():
            raise ValueError(
                "patched is true but patch is empty; include the unified git "
                "diff in the patch field"
            )
        return cleaned

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        files = [str(f) for f in output.get("files_changed") or []]
        patched = bool(output.get("patched", False))
        summary = str(output["diff_summary"]).strip()
        findings = [
            self.finding(
                agent,
                FindingKind.PATCH,
                summary.splitlines()[0][:300],
                detail="\n\n".join(
                    part
                    for part in (
                        summary,
                        (
                            f"Residual risk: {output['residual_risk']}"
                            if output.get("residual_risk")
                            else ""
                        ),
                        "" if patched else "Agent reports it did NOT land a patch.",
                    )
                    if part
                ),
                confidence=confidence if patched else min(confidence, 0.2),
                files=files,
            )
        ]
        # The fixer's own verification is cheap evidence only: the Reviewer
        # weighs it against the full suite.
        seeds_pass = output.get("cluster_seeds_passing")
        regression = str(output.get("regression_check") or "").strip()
        if seeds_pass is not None or regression:
            findings.append(
                self.finding(
                    agent,
                    FindingKind.VERIFICATION,
                    "Cluster seeds pass after the patch"
                    if seeds_pass
                    else "Cluster seeds still fail after the patch",
                    detail=regression or "No regression sample reported.",
                    confidence=confidence if seeds_pass else 0.1,
                    files=files,
                )
            )
        return findings


def agent_slug(kwargs: dict[str, Any]) -> str:
    """Worktree name component: one worktree per cluster, never shared."""
    cluster_id = kwargs.get("cluster_id")
    return str(cluster_id) if cluster_id else "unclustered"
