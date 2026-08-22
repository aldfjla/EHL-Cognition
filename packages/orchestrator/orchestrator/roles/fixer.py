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
from orchestrator.schemas import Agent, Finding, Role


class FixerAgent(RoleAgent):
    """Fix Engineer."""

    role = Role.FIXER
    prompt_file = "fixer.md"
    display_name = "Fix Engineer"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/fixer.md``."""
        raise NotImplementedError
        # TODO(build): pass root_cause, files, constraints, worktree, scenario_seeds, iteration.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
