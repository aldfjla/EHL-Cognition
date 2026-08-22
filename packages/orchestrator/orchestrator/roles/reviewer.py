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
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class ReviewerAgent(RoleAgent):
    """Tech Lead."""

    role = Role.REVIEWER
    prompt_file = "reviewer.md"
    display_name = "Tech Lead"
    required_keys = ("verdict",)

    #: The only verdicts ``pipeline`` knows how to act on.
    verdicts = ("ship", "iterate", "give_up")

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/reviewer.md``."""
        return {
            "fix_summary": kwargs.get("fix_summary")
            or [
                f.summary
                for f in self.ctx.blackboard.all()
                if f.kind is FindingKind.PATCH
            ],
            "before_stats": kwargs.get("before_stats"),
            "after_stats": kwargs.get("after_stats") or self.ctx.run.suite,
            "regressions": kwargs.get("regressions") or [],
            "conflicts": kwargs.get("conflicts") or [],
            # The reviewer judges the code, so it gets the diff itself.
            "diff": kwargs.get("diff") or "(diff unavailable)",
            "iteration": kwargs.get("iteration", self.ctx.fix_iteration),
            "max_iterations": kwargs.get("max_iterations", self.ctx.max_fix_iterations),
        }

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """The verdict gates the run, so an unknown value cannot be accepted."""
        cleaned = super().validate_output(output)
        verdict = str(cleaned["verdict"]).strip().lower()
        if verdict not in self.verdicts:
            raise ValueError(f"verdict must be one of {', '.join(self.verdicts)}")
        cleaned["verdict"] = verdict
        return cleaned

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        rejected = output.get("rejected_findings") or []
        detail = "\n\n".join(
            part
            for part in (
                str(output.get("conflict_resolution") or ""),
                (
                    f"Still failing: {output['remaining_failures']}"
                    if output.get("remaining_failures")
                    else ""
                ),
                "\n".join(
                    f"Rejected {item.get('id') if isinstance(item, dict) else item}: "
                    f"{item.get('why', '') if isinstance(item, dict) else ''}"
                    for item in rejected
                ),
            )
            if part
        )
        return [
            self.finding(
                agent,
                FindingKind.VERIFICATION,
                f"Full-suite verdict: {output['verdict']}",
                detail=detail,
                confidence=confidence,
            )
        ]

    async def adjudicate(self, output: dict[str, Any]) -> None:
        """Apply the verdict to the board: confirm, refute, supersede.

        The Reviewer is the only seat allowed to promote a ``root_cause`` past
        ``proposed``; everything the report says rests on this step.
        """
        board = self.ctx.blackboard
        agent_id = self.session.agent.id if self.session else ""
        for finding_id in output.get("accepted_findings") or []:
            await board.confirm(str(finding_id), agent_id)
        for item in output.get("rejected_findings") or []:
            if isinstance(item, dict):
                await board.refute(str(item.get("id")), str(item.get("why", "")))
            else:
                await board.refute(str(item), "rejected by the tech lead")
        by_id = {finding.id: finding for finding in board.all()}
        for pair in output.get("superseded") or []:
            if not isinstance(pair, dict):
                continue
            replacement = by_id.get(str(pair.get("new")))
            if replacement is not None:
                await board.supersede(str(pair.get("old")), replacement)
