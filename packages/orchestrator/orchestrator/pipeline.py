"""The stage machine that drives one CI run end to end.

Responsibility
--------------
Own the ordering, the transitions and the fan-out decisions of a run. Every
stage either calls into :mod:`simkit` (the oracle — deterministic, no agents)
or dispatches one or more roles from :mod:`orchestrator.roles` (agents —
non-deterministic, must have their work checked by the oracle).

That split is the whole thesis: **agents propose, simulation disposes.** No
stage may accept an agent's claim without a simkit result backing it.

Inputs
------
A trigger payload from ``POST /webhooks/github``: repo, branch, commit sha.
Plus ``robotci.yaml`` read from the customer repo checkout.

Outputs
-------
A terminal :class:`~orchestrator.schemas.Stage`, a :class:`Report`, and — on
the fixed path — an open pull request. Every intermediate step is published to
:mod:`orchestrator.bus` as an :class:`Event` for the dashboard.

Flow
----
``TRIGGERED -> RESOLVE_MODEL -> BUILD_HARNESS -> DESIGN_SCENARIOS -> RUN_SUITE
-> CLUSTER_FAILURES -> INVESTIGATE (fan-out) -> FIX (fan-out) -> VERIFY
-> REPORT -> PR_OPENED``

with two early exits: a clean suite short-circuits to ``PASSED_CLEAN``, and
exhausting the iteration budget lands on ``FAILED_UNRESOLVED``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from orchestrator.schemas import (
    Cluster,
    Report,
    Run,
    Scenario,
    Stage,
)

if TYPE_CHECKING:
    from orchestrator.blackboard import Blackboard
    from orchestrator.bus import EventBus
    from orchestrator.devin.client import DevinClient
    from orchestrator.workspace import Workspace


# --------------------------------------------------------------------------- #
# Legal transitions
# --------------------------------------------------------------------------- #

#: Adjacency map of permitted stage transitions. ``pipeline`` refuses any move
#: not listed here, so a bug in a role cannot skip verification.
TRANSITIONS: dict[Stage, tuple[Stage, ...]] = {
    Stage.TRIGGERED: (Stage.RESOLVE_MODEL, Stage.FAILED_UNRESOLVED),
    Stage.RESOLVE_MODEL: (Stage.BUILD_HARNESS, Stage.FAILED_UNRESOLVED),
    Stage.BUILD_HARNESS: (Stage.DESIGN_SCENARIOS, Stage.FAILED_UNRESOLVED),
    Stage.DESIGN_SCENARIOS: (Stage.RUN_SUITE, Stage.FAILED_UNRESOLVED),
    # A clean suite exits immediately — nothing to investigate.
    Stage.RUN_SUITE: (Stage.CLUSTER_FAILURES, Stage.PASSED_CLEAN),
    Stage.CLUSTER_FAILURES: (Stage.INVESTIGATE, Stage.PASSED_CLEAN),
    Stage.INVESTIGATE: (Stage.FIX, Stage.FAILED_UNRESOLVED),
    Stage.FIX: (Stage.VERIFY, Stage.FAILED_UNRESOLVED),
    # VERIFY loops back to FIX while the iteration budget holds.
    Stage.VERIFY: (Stage.REPORT, Stage.FIX, Stage.FAILED_UNRESOLVED),
    Stage.REPORT: (Stage.PR_OPENED, Stage.FAILED_UNRESOLVED),
    Stage.PR_OPENED: (),
    Stage.PASSED_CLEAN: (),
    Stage.FAILED_UNRESOLVED: (),
}


def can_transition(src: Stage, dst: Stage) -> bool:
    """True if ``src -> dst`` is a legal move."""
    return dst in TRANSITIONS.get(src, ())


# --------------------------------------------------------------------------- #
# Run context
# --------------------------------------------------------------------------- #


@dataclass
class PipelineContext:
    """Everything one run needs, passed to every stage handler.

    Held in memory for the life of the run; durable state lives in the store.
    """

    run: Run
    workspace: Workspace
    bus: EventBus
    blackboard: Blackboard
    devin: DevinClient
    config: dict = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    report: Report | None = None
    #: Guards the FIX <-> VERIFY loop against running forever.
    fix_iteration: int = 0
    max_fix_iterations: int = 3


class Pipeline:
    """Executes the stage machine for a single run.

    Not reusable across runs — construct one per trigger.
    """

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    # -- driver ------------------------------------------------------------ #

    async def run(self) -> Run:
        """Drive the run to a terminal stage and return the final Run.

        Dispatches to the ``stage_*`` handler for the current stage until
        ``run.stage.is_terminal``. Infrastructure errors are caught, recorded
        on ``run.error`` and land the run in ``FAILED_UNRESOLVED`` — they must
        never be reported as a robot failure.
        """
        raise NotImplementedError
        # TODO(build): loop on current stage, dispatch handler, await next
        # stage, call advance(); wrap in try/except to record infra errors.

    async def advance(self, dst: Stage) -> None:
        """Move to ``dst``, rejecting illegal transitions, and emit an event.

        Raises ``ValueError`` if ``can_transition`` says no.
        """
        raise NotImplementedError
        # TODO(build): validate via can_transition, mutate run.stage, persist,
        # publish EventType.RUN_STAGE_CHANGED on the bus.

    # -- stage handlers ---------------------------------------------------- #
    # Each returns the next Stage. Agent stages dispatch roles; oracle stages
    # call simkit directly.

    async def stage_triggered(self) -> Stage:
        """Clone the customer repo at the pushed SHA and read ``robotci.yaml``.

        Pure setup, no agents. Fails the run if the repo has no readable
        entrypoint — there is nothing to test.
        """
        raise NotImplementedError
        # TODO(build): workspace.clone(), parse robotci.yaml into ctx.config,
        # validate control.entrypoint exists.

    async def stage_resolve_model(self) -> Stage:
        """Find a physical model for the robot the code drives.

        Library first: ask :mod:`simkit.models.resolver` for a Menagerie match
        without spending an agent. Only when that misses do we dispatch the
        Modeler role to synthesize MJCF from the repo's kinematics.
        """
        raise NotImplementedError
        # TODO(build): try simkit resolver; on miss dispatch roles.modeler and
        # validate the produced MJCF loads in MuJoCo before accepting it.

    async def stage_build_harness(self) -> Stage:
        """Bind the pushed control code to the simulated robot.

        Dispatches the Harness Builder role: it writes the adapter that makes
        the customer's ``control.entrypoint`` drive MuJoCo actuators instead of
        a real driver. Accepted only once a smoke scenario executes.
        """
        raise NotImplementedError
        # TODO(build): dispatch roles.harness_builder, then prove the harness
        # by running one trivial scenario through simkit.runner.

    async def stage_design_scenarios(self) -> Stage:
        """Design the randomized world matrix.

        Dispatches the Scenario Designer role to choose *which* axes to
        randomize and over what ranges; the concrete sampling is done
        deterministically by :mod:`simkit.scenarios` from a seed.
        """
        raise NotImplementedError
        # TODO(build): dispatch roles.scenario_designer for ranges, then call
        # simkit.scenarios.generate(seed, count, ranges) -> ctx.scenarios.

    async def stage_run_suite(self) -> Stage:
        """Execute every scenario in parallel. The oracle speaks here.

        No agents involved. Emits ``suite.progress`` as cells complete so the
        matrix fills in live. Returns ``PASSED_CLEAN`` when nothing failed.
        """
        raise NotImplementedError
        # TODO(build): simkit.suite.run_suite() with progress callback ->
        # bus; branch on failure count.

    async def stage_cluster_failures(self) -> Stage:
        """Group failing scenarios by suspected shared cause.

        Cheap and deterministic — clustering on diagnosis text, not an agent.
        Cluster count sets the Investigator fan-out width, so it directly
        controls how many Devin sessions we spend.
        """
        raise NotImplementedError
        # TODO(build): clustering.cluster_failures(failed_scenarios); cap
        # cluster count at MAX_PARALLEL_AGENTS.

    async def stage_investigate(self) -> Stage:
        """Fan out one Investigator per cluster to find root causes.

        Runs concurrently, bounded by ``MAX_PARALLEL_AGENTS``. Each agent
        writes a ``root_cause`` finding to the blackboard; the orchestrator
        relays cross-cluster findings between them as Messages.
        """
        raise NotImplementedError
        # TODO(build): asyncio.gather over clusters with a semaphore; relay
        # findings between live investigators via bus + blackboard.

    async def stage_fix(self) -> Stage:
        """Fan out one Fixer per confirmed root cause.

        Each Fixer patches the customer repo on a branch and self-verifies by
        re-running only its own cluster's scenarios — cheap, fast feedback
        before the expensive full-suite gate at VERIFY.
        """
        raise NotImplementedError
        # TODO(build): dispatch roles.fixer per confirmed finding; each fixer
        # re-runs its cluster's seeds until they pass or iterations run out.

    async def stage_verify(self) -> Stage:
        """Re-run the FULL suite against all accepted patches together.

        This is the gate that catches fixes which break other scenarios — the
        single most important stage, because independent agents patching the
        same repo will otherwise conflict. Loops back to FIX while the budget
        holds; the Reviewer role adjudicates conflicts and dedupes patches.
        """
        raise NotImplementedError
        # TODO(build): merge patches, re-run full suite, compare against
        # baseline; dispatch roles.reviewer on regression; increment
        # ctx.fix_iteration and loop to FIX or give up.

    async def stage_report(self) -> Stage:
        """Write the incident report from the confirmed blackboard findings."""
        raise NotImplementedError
        # TODO(build): dispatch roles.reporter -> Report; render markdown to
        # ARTIFACTS_DIR; attach before/after videos per incident.

    async def stage_pr_opened(self) -> Stage:
        """Push the branch and open the pull request. Terminal."""
        raise NotImplementedError
        # TODO(build): github.open_pull_request() with the report as body;
        # set run.pull_request_url.


# TODO(build): add a headless entrypoint — `python -m orchestrator.pipeline
# --repo owner/name --sha <sha>` — so the pipeline can be demoed without the
# API process running.
