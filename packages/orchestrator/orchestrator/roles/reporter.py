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
from orchestrator.schemas import (
    Agent,
    Finding,
    FindingStatus,
    Incident,
    Report,
    Role,
    Verdict,
)


class ReporterAgent(RoleAgent):
    """Engineering Manager."""

    role = Role.REPORTER
    prompt_file = "reporter.md"
    display_name = "Engineering Manager"
    required_keys = ("verdict", "title", "summary")

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/reporter.md``."""
        confirmed = [
            f"[{f.kind.value}] {f.summary}"
            for f in self.ctx.blackboard.all()
            if f.status is FindingStatus.CONFIRMED
        ]
        return {
            # Only confirmed findings: an unverified theory in the PR body is
            # worse than an honest gap.
            "confirmed_findings": kwargs.get("confirmed_findings") or confirmed,
            "before_stats": kwargs.get("before_stats"),
            "after_stats": kwargs.get("after_stats") or self.ctx.run.suite,
            "diff": kwargs.get("diff") or "(diff unavailable)",
            # Absolute URLs, or the clips 404 inside the GitHub PR body.
            "video_pairs": kwargs.get("video_pairs") or [],
        }

    def validate_output(self, output: dict[str, Any]) -> dict[str, Any]:
        """The verdict must be a :class:`Verdict`; the PR body depends on it."""
        cleaned = super().validate_output(output)
        try:
            cleaned["verdict"] = Verdict(str(cleaned["verdict"]).strip().lower()).value
        except ValueError as exc:
            raise ValueError(
                "verdict must be one of " + ", ".join(v.value for v in Verdict)
            ) from exc
        return cleaned

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """None: the Reporter reads the board and adds no new knowledge."""
        return []

    def to_report(self, output: dict[str, Any], **kwargs: Any) -> Report:
        """Build the :class:`Report` the PR body is rendered from."""
        incidents = []
        for raw in output.get("incidents") or []:
            if not isinstance(raw, dict):
                continue
            cluster_id = str(raw.get("cluster_id", ""))
            cluster = next(
                (c for c in self.ctx.clusters if c.id == cluster_id),
                None,
            )
            videos = (kwargs.get("video_pairs") or {}).get(cluster_id) or {}
            incidents.append(
                Incident(
                    cluster_id=cluster_id,
                    title=str(raw.get("title", "")),
                    affected_scenarios=cluster.size if cluster else 0,
                    root_cause=str(raw.get("root_cause", "")),
                    resolution=str(raw.get("resolution", "")),
                    files_changed=[str(f) for f in raw.get("files_changed") or []],
                    before_video=videos.get("before"),
                    after_video=videos.get("after"),
                    status=("fixed" if raw.get("status") == "fixed" else "unresolved"),
                )
            )
        return Report(
            run_id=self.ctx.run.id,
            verdict=Verdict(output["verdict"]),
            title=str(output["title"]),
            summary=str(output["summary"]),
            incidents=incidents,
            diff=kwargs.get("diff"),
            before=kwargs.get("before_stats"),
            after=kwargs.get("after_stats") or self.ctx.run.suite,
        )
