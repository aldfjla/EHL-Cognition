"""Tech Lead — re-run the full suite, adjudicate conflicts, gate the run.

Stage: ``VERIFY``.

The only seat that sees every patch merged together, which is the only state
that ships. Judges the combined suite result, resolves overlapping patches,
dedupes root causes that turn out to describe one bug, and rejects fixes that
pass the suite while being obviously wrong — hardcoding to test seeds, weakening
criteria, sim-only special cases.

Returns one of three verdicts: ship, iterate (back to FIX with notes, while the
budget holds), or give up (FAILED_UNRESOLVED with an honest report). The last is
a legitimate outcome and is preferred over a fake green.

Inputs:  merged patches, before/after suite stats, regressions, conflicts.
Outputs: accepted and rejected findings, conflict resolutions, a verdict.

Findings written
----------------
`verification` — the full-suite verdict and the reasoning.
Promotes `root_cause` findings to `confirmed`, or `refuted` when the fix did
not hold. Marks duplicates `superseded` so the report says one thing per bug.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, Role


class ReviewerAgent(RoleAgent):
    """Tech Lead."""

    role = Role.REVIEWER
    prompt_file = "reviewer.md"
    display_name = "Tech Lead"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/reviewer.md``."""
        raise NotImplementedError
        # TODO(build): pass fix_summary, before_stats, after_stats, regressions, conflicts, diff.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
