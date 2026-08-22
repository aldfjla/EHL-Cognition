"""Debug Engineer — find the root cause of ONE failure cluster.

Stage: ``INVESTIGATE``.

Fanned out one per cluster, bounded by `MAX_PARALLEL_AGENTS`. Each agent owns a
single cluster and is told only about that cluster plus the team's constraints.
The narrow scope is deliberate: prompt budget spent on other clusters' detail is
budget not spent on this one.

Required to reproduce the failure from its seed before theorising, and to test
its theory by moving one variable and predicting the result. Explicitly
forbidden from patching: the fix is a separate seat, so that the explanation and
the change can be judged independently.

Inputs:  a `Cluster`, its scenarios' diagnoses, the param correlation.
Outputs: a root cause with file:line references and evidence.

Findings written
----------------
`root_cause` — the mechanism, confidence-scored. Promoted to `confirmed` only
after the Reviewer sees the fix hold at full-suite scale.
`observation` — anything noticed outside this cluster, relayed to the peers
working the other clusters.
"""

from __future__ import annotations

from typing import Any

from orchestrator.roles.base import RoleAgent
from orchestrator.schemas import Agent, Finding, Role


class InvestigatorAgent(RoleAgent):
    """Debugging Engineer."""

    role = Role.INVESTIGATOR
    prompt_file = "investigator.md"
    display_name = "Debugging Engineer"

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/investigator.md``."""
        raise NotImplementedError
        # TODO(build): pass cluster_label, cluster_size, scenario_seeds, diagnoses, param_correlation.

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        raise NotImplementedError
        # TODO(build): map output keys onto Finding objects per the docstring.
