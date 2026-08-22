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

from typing import TYPE_CHECKING

from orchestrator.schemas import (
    EventType,
    Finding,
    FindingKind,
    FindingStatus,
    Role,
    Speaker,
)

if TYPE_CHECKING:
    from orchestrator.bus import EventBus

#: Most findings rendered into a single prompt. Prompt budget spent on a
#: fourteenth finding is budget not spent on the problem.
RENDER_LIMIT = 12


class Blackboard:
    """Append-mostly store of findings for one run."""

    def __init__(self, run_id: str, bus: EventBus | None = None) -> None:
        self.run_id = run_id
        self._bus = bus
        self._findings: list[Finding] = []
        self._by_id: dict[str, Finding] = {}
        self._by_cluster: dict[str, list[Finding]] = {}
        self._by_kind: dict[FindingKind, list[Finding]] = {}

    # -- writing ----------------------------------------------------------- #

    async def write(self, finding: Finding) -> Finding:
        """Record a finding and emit ``finding.created`` on the bus.

        Findings are never mutated in place except through :meth:`confirm`,
        :meth:`refute` and :meth:`supersede` — the board is an audit trail of
        what the team believed and when, and the report is written from it.
        """
        self._findings.append(finding)
        self._by_id[finding.id] = finding
        self._by_kind.setdefault(finding.kind, []).append(finding)
        if finding.cluster_id:
            self._by_cluster.setdefault(finding.cluster_id, []).append(finding)
        if self._bus is not None:
            await self._bus.emit(
                self.run_id,
                EventType.FINDING_CREATED,
                finding.model_dump(mode="json"),
            )
        return finding

    async def confirm(self, finding_id: str, by_agent_id: str) -> Finding:
        """Promote a finding to ``confirmed`` after the oracle backed it up."""
        finding = self._get(finding_id)
        finding.status = FindingStatus.CONFIRMED
        finding.detail = _append_note(finding.detail, f"Confirmed by {by_agent_id}.")
        await self._emit_updated(finding)
        return finding

    async def refute(self, finding_id: str, reason: str) -> Finding:
        """Mark a finding wrong. Keeps it on the board — refuted hypotheses are
        useful context that stops a later agent re-treading the same path."""
        finding = self._get(finding_id)
        finding.status = FindingStatus.REFUTED
        finding.detail = _append_note(finding.detail, f"Refuted: {reason}")
        await self._emit_updated(finding)
        return finding

    async def supersede(self, old_id: str, new: Finding) -> Finding:
        """Replace a finding with a better one, preserving the chain."""
        old = self._get(old_id)
        await self.write(new)
        old.status = FindingStatus.SUPERSEDED
        old.superseded_by = new.id
        await self._emit_updated(old)
        return new

    # -- reading ----------------------------------------------------------- #

    def all(self) -> list[Finding]:
        """Every finding, oldest first."""
        return list(self._findings)

    def for_cluster(self, cluster_id: str) -> list[Finding]:
        """Findings scoped to one failure cluster — what a Fixer needs."""
        return list(self._by_cluster.get(cluster_id, ()))

    def for_role(self, role: Role) -> list[Finding]:
        """Findings the relay policy says ``role`` should see.

        Not everything: an Investigator drowning in every other cluster's
        detail investigates worse. The policy lives in ``docs/AGENT_ROLES.md``
        and is enforced here.
        """
        if role is Role.MODELER:
            # First on the board; there is nothing to relay to it.
            return []
        if role is Role.HARNESS_BUILDER:
            return [
                f
                for f in self.constraints()
                if f.author_role is Speaker.MODELER
            ]
        if role is Role.SCENARIO_DESIGNER:
            return self.constraints()
        if role is Role.INVESTIGATOR:
            # Cluster detail is handed over separately, via for_cluster.
            return self.constraints() + self._active(FindingKind.OBSERVATION)
        if role is Role.FIXER:
            reviewer_notes = [
                f
                for f in self._active(FindingKind.VERIFICATION)
                if f.author_role is Speaker.REVIEWER
            ]
            return self.constraints() + reviewer_notes
        if role is Role.REVIEWER:
            return self.all()
        if role is Role.REPORTER:
            return [f for f in self._findings if f.status is FindingStatus.CONFIRMED]
        return []

    def constraints(self) -> list[Finding]:
        """Active ``constraint`` findings — invariants no agent may violate."""
        return self._active(FindingKind.CONSTRAINT)

    def confirmed_root_causes(self) -> list[Finding]:
        """Confirmed root causes, highest confidence first. Drives FIX fan-out."""
        causes = [
            f
            for f in self._by_kind.get(FindingKind.ROOT_CAUSE, ())
            if f.status is FindingStatus.CONFIRMED
        ]
        return sorted(causes, key=lambda f: f.confidence, reverse=True)

    # -- prompt rendering -------------------------------------------------- #

    def render_context(self, role: Role, cluster_id: str | None = None) -> str:
        """Render the board into markdown to splice into an agent's prompt.

        This function is where collaboration actually happens — its output is
        literally what one agent "says" to another. Keep it short: prompt
        budget spent on irrelevant findings is prompt budget not spent on the
        problem.
        """
        selected = self.for_role(role)
        if cluster_id:
            for finding in self.for_cluster(cluster_id):
                if finding not in selected:
                    selected.append(finding)
        selected = [f for f in selected if f.status is not FindingStatus.SUPERSEDED]
        if not selected:
            return ""

        # Constraints first: they are the findings that make other work wrong.
        def order(finding: Finding) -> tuple[int, int]:
            kind_rank = 0 if finding.kind is FindingKind.CONSTRAINT else 1
            return (kind_rank, self._findings.index(finding))

        lines = ["## What the team has established"]
        for finding in sorted(selected, key=order)[:RENDER_LIMIT]:
            lines.append(_render_finding(finding))
        return "\n".join(lines)

    # -- internals --------------------------------------------------------- #

    def _get(self, finding_id: str) -> Finding:
        finding = self._by_id.get(finding_id)
        if finding is None:
            raise KeyError(f"no finding {finding_id} on the board for {self.run_id}")
        return finding

    def _active(self, kind: FindingKind) -> list[Finding]:
        return [
            f
            for f in self._by_kind.get(kind, ())
            if f.status not in (FindingStatus.REFUTED, FindingStatus.SUPERSEDED)
        ]

    async def _emit_updated(self, finding: Finding) -> None:
        if self._bus is None:
            return
        await self._bus.emit(
            self.run_id,
            EventType.FINDING_UPDATED,
            {
                "finding_id": finding.id,
                "status": finding.status.value,
                "superseded_by": finding.superseded_by,
            },
        )


def _append_note(detail: str, note: str) -> str:
    return f"{detail}\n\n{note}".strip()


def _render_finding(finding: Finding) -> str:
    role = finding.author_role.value
    line = f"- [{role}, {finding.kind.value}, confidence {finding.confidence:.2f}]"
    line = f"{line} {finding.summary}"
    refs: list[str] = []
    if finding.files:
        refs.append("files: " + ", ".join(finding.files))
    if finding.scenario_ids:
        refs.append("scenarios: " + ", ".join(finding.scenario_ids))
    if refs:
        line = f"{line} ({'; '.join(refs)})"
    return line
