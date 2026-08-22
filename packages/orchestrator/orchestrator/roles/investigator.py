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
from orchestrator.schemas import Agent, Finding, FindingKind, Role


class InvestigatorAgent(RoleAgent):
    """Debugging Engineer."""

    role = Role.INVESTIGATOR
    prompt_file = "investigator.md"
    display_name = "Debugging Engineer"
    required_keys = ("root_cause", "evidence")

    def _scenarios(self, cluster: Any) -> list[Any]:
        """The run's scenarios belonging to ``cluster``."""
        if cluster is None:
            return []
        ids = set(getattr(cluster, "scenario_ids", ()) or ())
        return [s for s in self.ctx.scenarios if s.id in ids]

    def template_vars(self, **kwargs: Any) -> dict[str, Any]:
        """Substitutions for ``devin/prompts/investigator.md``."""
        cluster = kwargs.get("cluster")
        scenarios = self._scenarios(cluster)
        model = self.ctx.run.robot_model
        return {
            "cluster_label": kwargs.get("cluster_label")
            or (cluster.label if cluster is not None else "unlabelled cluster"),
            "cluster_size": kwargs.get("cluster_size")
            or (cluster.size if cluster is not None else len(scenarios)),
            "suite_total": kwargs.get("suite_total")
            or (self.ctx.run.suite.total if self.ctx.run.suite else len(scenarios)),
            "scenario_seeds": [s.seed for s in scenarios],
            "diagnoses": [
                f"seed {s.seed}: {s.diagnosis}" for s in scenarios if s.diagnosis
            ],
            "param_correlation": kwargs.get("param_correlation"),
            # Joint traces are where the real answer usually is, so hand over
            # the files rather than only the diagnosis string.
            "trace_paths": kwargs.get("trace_paths")
            or [s.trace_path for s in scenarios if s.trace_path],
            "model_path": kwargs.get("model_path")
            or (model.model_path if model else ""),
            "harness_path": kwargs.get("harness_path", ""),
        }

    def to_findings(self, agent: Agent, output: dict[str, Any]) -> list[Finding]:
        """Convert structured output into blackboard findings."""
        confidence = float(output.get("confidence", 0.5) or 0.5)
        reproduced = bool(output.get("reproduced", False))
        files = [str(f) for f in output.get("files") or []]
        detail = "\n\n".join(
            part
            for part in (
                str(output.get("evidence") or ""),
                (
                    f"Suggested direction: {output['suggested_direction']}"
                    if output.get("suggested_direction")
                    else ""
                ),
                "" if reproduced else "NOT reproduced from its seed — treat as flaky.",
            )
            if part
        )
        findings = [
            self.finding(
                agent,
                FindingKind.ROOT_CAUSE,
                str(output["root_cause"]).strip().splitlines()[0][:300],
                detail=f"{output['root_cause']}\n\n{detail}".strip(),
                # An unreproduced failure is a claim about nothing: the
                # simulator never showed it twice.
                confidence=confidence if reproduced else min(confidence, 0.3),
                files=files,
            )
        ]
        # Relayed to the peers working the other clusters.
        for observation in output.get("observations") or []:
            findings.append(
                self.finding(
                    agent,
                    FindingKind.OBSERVATION,
                    str(observation),
                    detail="Noticed outside this cluster while investigating "
                    f"{agent.cluster_id or 'it'}.",
                    confidence=confidence,
                )
            )
        return findings
