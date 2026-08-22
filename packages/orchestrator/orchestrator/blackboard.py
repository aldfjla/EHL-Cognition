"""Shared findings store — the substrate that makes the agents a team.

Responsibility
--------------
Devin sessions are isolated: they have no channel to each other, no shared
memory, and no way to address a peer. The blackboard is how they collaborate
anyway. Every agent writes what it learns here as a
:class:`~orchestrator.schemas.Finding`; the orchestrator reads the board and
splices relevant findings into other sessions' prompts.

Inputs:  ``Finding`` objects parsed out of agent output.
Outputs: filtered views of the board (``for_role``, ``for_cluster``) that the
role modules render into prompt context, plus ``finding.created`` events.

Why a blackboard and not a message queue
----------------------------------------
Agents come and go mid-run. An Investigator that finishes before a Fixer starts
still needs to hand over its conclusion, and a Fixer spawned later needs the
constraints an earlier agent established. A durable board handles both without
either agent being alive at the same moment. This is the classic blackboard
architecture, and it is honest about what Devin sessions actually are.

See ``docs/AGENT_ROLES.md`` for the relay rules.
"""

from __future__ import annotations

from orchestrator.schemas import Finding, Role


class Blackboard:
    """Append-mostly store of findings for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        # TODO(build): list[Finding] plus indexes by cluster_id and kind.

    # -- writing ----------------------------------------------------------- #

    async def write(self, finding: Finding) -> Finding:
        """Record a finding and emit ``finding.created`` on the bus.

        Findings are never mutated in place except through :meth:`confirm`,
        :meth:`refute` and :meth:`supersede` — the board is an audit trail of
        what the team believed and when, and the report is written from it.
        """
        raise NotImplementedError
        # TODO(build): append, index, publish event.

    async def confirm(self, finding_id: str, by_agent_id: str) -> Finding:
        """Promote a finding to ``confirmed`` after the oracle backed it up."""
        raise NotImplementedError
        # TODO(build): set status, emit finding.updated.

    async def refute(self, finding_id: str, reason: str) -> Finding:
        """Mark a finding wrong. Keeps it on the board — refuted hypotheses are
        useful context that stops a later agent re-treading the same path."""
        raise NotImplementedError
        # TODO(build): set status + record reason in detail.

    async def supersede(self, old_id: str, new: Finding) -> Finding:
        """Replace a finding with a better one, preserving the chain."""
        raise NotImplementedError
        # TODO(build): link superseded_by, write the new finding.

    # -- reading ----------------------------------------------------------- #

    def all(self) -> list[Finding]:
        """Every finding, oldest first."""
        raise NotImplementedError
        # TODO(build): return a copy so callers cannot mutate the board.

    def for_cluster(self, cluster_id: str) -> list[Finding]:
        """Findings scoped to one failure cluster — what a Fixer needs."""
        raise NotImplementedError
        # TODO(build): index lookup.

    def for_role(self, role: Role) -> list[Finding]:
        """Findings the relay policy says ``role`` should see.

        Not everything: an Investigator drowning in every other cluster's
        detail investigates worse. The policy lives in ``docs/AGENT_ROLES.md``
        and is enforced here.
        """
        raise NotImplementedError
        # TODO(build): implement the per-role relay filter from AGENT_ROLES.md.

    def constraints(self) -> list[Finding]:
        """Active ``constraint`` findings — invariants no agent may violate."""
        raise NotImplementedError
        # TODO(build): filter kind == CONSTRAINT and status != REFUTED.

    def confirmed_root_causes(self) -> list[Finding]:
        """Confirmed root causes, highest confidence first. Drives FIX fan-out."""
        raise NotImplementedError
        # TODO(build): filter + sort by confidence desc.

    # -- prompt rendering -------------------------------------------------- #

    def render_context(self, role: Role, cluster_id: str | None = None) -> str:
        """Render the board into markdown to splice into an agent's prompt.

        This function is where collaboration actually happens — its output is
        literally what one agent "says" to another. Keep it short: prompt
        budget spent on irrelevant findings is prompt budget not spent on the
        problem.
        """
        raise NotImplementedError
        # TODO(build): select via for_role/for_cluster, cap at N findings,
        # format each as "- [role, confidence] summary" with refs.


# TODO(build): persist the board to the store so a run survives an API restart;
# in-memory only is fine for the demo but loses the audit trail on crash.
