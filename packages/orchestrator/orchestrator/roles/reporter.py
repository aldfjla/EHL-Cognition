"""Engineering Manager — write the incident report and open the PR.

Stage: ``REPORT``.

Turns the confirmed blackboard findings into the document a human actually
reads. The report is used verbatim as the pull request body, so what the
dashboard shows and what the developer reads are the same text.

Instructed to lead with findings rather than process, cite seeds and file:line,
reference the before/after video for each incident, and state plainly what is
still broken. Agent theatre is explicitly excluded — the reader wants an
engineering report, not a description of how many sessions ran.

Inputs:  confirmed findings, suite stats, the diff, video pairs.
Outputs: a `Report`.

Findings written
----------------
None. The Reporter reads the board and writes the document; it is the end of
the chain and adds no new knowledge.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, Role


class ReporterAgent(RoleAgent):
    """Engineering Manager."""

    role = Role.REPORTER
    prompt_file = "reporter.md"
    display_name = "Engineering Manager"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/reporter.md``."""
        raise NotImplementedError
        # TODO(build): pass confirmed_findings, before_stats, after_stats, diff, video_pairs.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
