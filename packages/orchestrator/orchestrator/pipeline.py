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
red or regressed verification routes through ``REPORT`` to
``FAILED_UNRESOLVED``. An infrastructure crash may transition directly to
``FAILED_UNRESOLVED`` from any stage that permits it, including ``VERIFY``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator import clustering, github
from orchestrator import workspace as workspace_mod
from orchestrator.devin.hierarchy import AgentTree
from orchestrator.pool import SuitePool
from orchestrator.roles.base import RoleAgent
from orchestrator.roles.fixer import FixerAgent
from orchestrator.roles.harness_builder import HarnessBuilderAgent
from orchestrator.roles.investigator import InvestigatorAgent
from orchestrator.roles.modeler import ModelerAgent
from orchestrator.roles.reporter import ReporterAgent
from orchestrator.roles.reviewer import ReviewerAgent
from orchestrator.roles.scenario_designer import ScenarioDesignerAgent
from orchestrator.schemas import (
    Agent,
    Cluster,
    CriterionResult,
    EventType,
    Finding,
    FindingKind,
    Incident,
    ModelSource,
    Report,
    RobotModel,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Stage,
    SuiteStats,
    Verdict,
    _now,
)

if TYPE_CHECKING:
    from orchestrator.blackboard import Blackboard
    from orchestrator.bus import EventBus
    from orchestrator.devin.client import DevinClient
    from orchestrator.workspace import Workspace

log = logging.getLogger(__name__)


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
    # Only a full green VERIFY may proceed to REPORT and then PR_OPENED; a red
    # suite also goes through REPORT, so FAILED_UNRESOLVED here means a crash.
    Stage.VERIFY: (Stage.REPORT, Stage.FIX, Stage.FAILED_UNRESOLVED),
    # REPORT writes findings for both green and failed verification; only the
    # former returns PR_OPENED.
    Stage.REPORT: (Stage.PR_OPENED, Stage.FAILED_UNRESOLVED),
    Stage.PR_OPENED: (),
    Stage.PASSED_CLEAN: (),
    Stage.FAILED_UNRESOLVED: (),
}


def can_transition(src: Stage, dst: Stage) -> bool:
    """True if ``src -> dst`` is a legal move."""
    return dst in TRANSITIONS.get(src, ())


class PipelineError(RuntimeError):
    """Infrastructure failure. Never means the robot failed a test."""


#: Filename the Modeler writes its synthesized MJCF to, relative to the model
#: output directory handed to it. Fixed by convention so the pipeline can
#: validate the model in MuJoCo without parsing the agent's prose.
MODEL_FILENAME = "robot.xml"

#: Filename the Harness Builder writes its adapter to.
HARNESS_FILENAME = "harness.py"

#: Root causes at or above this confidence are promoted to ``confirmed`` so FIX
#: has something to fan out over. The Reviewer refutes them at VERIFY if the
#: patch does not hold at full-suite scale — the oracle still has the last word.
CONFIRM_CONFIDENCE = 0.5

#: Emit ``suite.progress`` at most this often, in completed scenarios.
PROGRESS_EVERY = 1
_CLUSTER_TERMINAL = {"resolved", "unresolved", "conflicted"}
_AGENT_WATCH_INTERVAL_S = 0.01


def _conflict_worktree(
    conflict: workspace_mod.PatchConflict, worktree: str | None
) -> bool:
    """Match a structured conflict to its rejected worktree."""
    if worktree is None:
        return False
    return conflict.worktree == worktree


def _conflict_description(conflict: workspace_mod.PatchConflict) -> str:
    """Render a conflict with the files and sibling that blocked it."""
    files = ", ".join(conflict.files) or "unknown files"
    blocked_by = ", ".join(conflict.blocked_by) or "unknown sibling"
    return f"{conflict.worktree} ({files}; blocked by {blocked_by})"


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
    sim_workers: int | None = None
    #: Called once with the parsed ``robotci.yaml`` as soon as there is a
    #: checkout. The transport layer uses it to cache the repo's trigger
    #: filters, which the webhook cannot read (nothing is cloned yet). The
    #: pipeline stays ignorant of the store; a failure here is logged, never
    #: fatal — the run is already legitimately in flight.
    on_config: Callable[[dict], Awaitable[None]] | None = None
    suite_size: int | None = None
    default_suite_size: int = 50
    scenarios: list[Scenario] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    report: Report | None = None
    #: Guards the FIX <-> VERIFY loop against running forever.
    fix_iteration: int = 0
    max_fix_iterations: int = 3


@dataclass
class _ClusterWork:
    """Pipeline-local progress for one independent failure cluster.

    The global run stage is the maximum phase reached by any cluster and moves
    only forward. A cluster may therefore enter ``FIX`` after another cluster
    has already reached ``VERIFY`` without trying to move the run backwards.
    """

    cluster: Cluster
    original_seeds: list[int]
    phase: str = "pending"
    agent_ids: list[str] = field(default_factory=list)
    cause: Finding | None = None
    worktree: str | None = None
    outcome: str | None = None
    error: str | None = None
    retry_count: int = 0
    owner_agent_id: str | None = None


class Pipeline:
    """Executes the stage machine for a single run.

    Not reusable across runs — construct one per trigger.
    """

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self._max_parallel = int(os.getenv("MAX_PARALLEL_AGENTS", "6"))
        self._max_parallel_agents = max(1, int(os.getenv("MAX_PARALLEL_AGENTS", "6")))
        self._artifacts = Path(os.getenv("ARTIFACTS_DIR", "artifacts")) / ctx.run.id
        #: Suite results before any patch, and after the latest VERIFY, kept as
        #: raw simkit results so ``simkit.suite.compare`` can diff them.
        self._before_results: list[Any] = []
        self._after_results: list[Any] = []
        self._after_videos: dict[str, dict[str, str]] = {}
        self._fix_worktrees: list[str] = []
        self._conflicts: list[workspace_mod.PatchConflict] = []
        self._landed_worktrees: list[str] = []
        self._agent_tree = AgentTree()
        self._pool: SuitePool | None = None
        self._agent_gate = asyncio.Semaphore(self._max_parallel_agents)
        self._cluster_work: dict[str, _ClusterWork] = {}
        self._cluster_tasks: dict[str, asyncio.Task[None]] = {}
        self._cluster_progress = asyncio.Event()
        self._merge_lock = asyncio.Lock()
        self._agent_updates: dict[str, dict[str, Any]] = {}

    # -- driver ------------------------------------------------------------ #

    def _handlers(self) -> dict[Stage, Callable[[], Awaitable[Stage]]]:
        return {
            Stage.TRIGGERED: self.stage_triggered,
            Stage.RESOLVE_MODEL: self.stage_resolve_model,
            Stage.BUILD_HARNESS: self.stage_build_harness,
            Stage.DESIGN_SCENARIOS: self.stage_design_scenarios,
            Stage.RUN_SUITE: self.stage_run_suite,
            Stage.CLUSTER_FAILURES: self.stage_cluster_failures,
            Stage.INVESTIGATE: self.stage_investigate,
            Stage.FIX: self.stage_fix,
            Stage.VERIFY: self.stage_verify,
            Stage.REPORT: self.stage_report,
        }

    async def run(self) -> Run:
        """Drive the run to a terminal stage and return the final Run.

        Dispatches to the ``stage_*`` handler for the current stage until
        ``run.stage.is_terminal``. Infrastructure errors are caught, recorded
        on ``run.error`` and land the run in ``FAILED_UNRESOLVED`` — they must
        never be reported as a robot failure.
        """
        run = self.ctx.run
        await self.ctx.bus.emit(
            run.id, EventType.RUN_CREATED, run.model_dump(mode="json")
        )
        handlers = self._handlers()
        try:
            while not run.stage.is_terminal:
                handler = handlers[run.stage]
                nxt = await handler()
                # PR_OPENED is terminal, so its side effects have to happen
                # before the transition or they would never run.
                if nxt is Stage.PR_OPENED:
                    nxt = await self.stage_pr_opened()
                await self.advance(nxt)
        except Exception as exc:
            log.exception("run %s failed in %s", run.id, run.stage.value)
            run.error = f"{type(exc).__name__}: {exc}"
            await self.ctx.bus.emit(
                run.id,
                EventType.ERROR,
                {"stage": run.stage.value, "message": run.error, "fatal": True},
            )
            await self._force_failed()
        await self._finish()
        return run

    async def advance(self, dst: Stage) -> None:
        """Move to ``dst``, rejecting illegal transitions, and emit an event.

        Raises ``ValueError`` if ``can_transition`` says no.
        """
        run = self.ctx.run
        src = run.stage
        if src is dst:
            return
        if not can_transition(src, dst):
            raise ValueError(f"illegal transition {src.value} -> {dst.value}")
        run.stage = dst
        run.updated_at = _now()
        await self.ctx.bus.emit(
            run.id,
            EventType.RUN_STAGE_CHANGED,
            {"stage": dst.value, "previous_stage": src.value},
        )

    # -- stage handlers ---------------------------------------------------- #
    # Each returns the next Stage. Agent stages dispatch roles; oracle stages
    # call simkit directly.

    async def stage_triggered(self) -> Stage:
        """Clone the customer repo at the pushed SHA and read ``robotci.yaml``.

        Pure setup, no agents. Fails the run if the repo has no readable
        entrypoint — there is nothing to test.
        """
        ctx = self.ctx
        run = ctx.run
        self._artifacts.mkdir(parents=True, exist_ok=True)
        ctx.workspace = await workspace_mod.clone(
            run.repo, run.commit_sha, run.id, ctx.workspace.root.parent
        )
        ctx.config = await workspace_mod.read_config(ctx.workspace)
        if ctx.on_config is not None:
            try:
                await ctx.on_config(ctx.config)
            except Exception:
                log.exception("caching robotci.yaml for %s failed", run.repo)
        policy = ctx.config.get("policy", {})
        self._max_parallel_agents = max(
            1,
            int(
                policy.get("max_parallel_agents", os.getenv("MAX_PARALLEL_AGENTS", "6"))
            ),
        )
        self._agent_gate = asyncio.Semaphore(self._max_parallel_agents)
        ctx.max_fix_iterations = int(
            ctx.config.get("policy", {}).get(
                "max_fix_iterations", ctx.max_fix_iterations
            )
        )

        entrypoint = ctx.config.get("control", {}).get("entrypoint", "")
        if not entrypoint:
            raise PipelineError(
                "robotci.yaml has no control.entrypoint and none could be "
                "inferred; there is no control code to simulate"
            )
        raw = entrypoint.split(":", 1)[0]
        # Dotted module notation ("pkg.mod") needs its dots turned into path
        # separators; a filesystem path ("src/ctl.py") must be left alone. The
        # replace used to be unconditional, which rewrote "src/ctl.py" to
        # "src/ctl/py" and made the path form documented in
        # robotci.example.yaml impossible to resolve.
        module = raw if "/" in raw or raw.endswith(".py") else raw.replace(".", "/")
        candidates = [
            ctx.workspace.base / module,
            ctx.workspace.base / f"{module}.py",
        ]
        if not any(path.exists() for path in candidates):
            raise PipelineError(f"control.entrypoint {entrypoint!r} does not exist")

        await github.set_commit_status(
            run.repo, run.commit_sha, "pending", "Robot CI: simulating"
        )
        return Stage.RESOLVE_MODEL

    async def stage_resolve_model(self) -> Stage:
        """Find a physical model before spending an agent.

        A durable cache hit follows the same resolved-model path and therefore
        skips Modeler dispatch entirely.
        """
        from simkit.models import generator, resolver

        ctx = self.ctx
        model_dir = self._artifacts / "model"
        model_dir.mkdir(parents=True, exist_ok=True)
        resolution = await asyncio.to_thread(
            resolver.resolve,
            ctx.workspace.base,
            ctx.config,
            model_dir,
            resolver.default_cache_dir(),
            ctx.run.repo,
        )
        if resolution.found:
            ctx.run.robot_model = RobotModel(
                source=ModelSource(resolution.source),
                name=resolution.name or None,
                model_path=resolution.model_path,
                dof=resolution.dof,
                confidence=resolution.confidence,
                provenance=resolution.provenance,
                license=resolution.license,
                processing_steps=resolution.processing_steps,
                approximate=resolution.approximate,
                cache_hit=resolution.cache_hit,
            )
            return Stage.BUILD_HARNESS

        agent = await ModelerAgent(ctx).dispatch(
            resolver_report=resolution.report,
            model_out_dir=str(model_dir),
            candidates=resolution.candidates,
        )
        model_path = model_dir / MODEL_FILENAME
        # The Modeler's word is not evidence: an unloadable model would fail
        # every downstream stage with a misleading error.
        ok, detail = await asyncio.to_thread(generator.validate, model_path)
        if not ok:
            raise PipelineError(f"Modeler produced an unloadable model: {detail}")
        ctx.run.robot_model = RobotModel(
            source=ModelSource.GENERATED,
            name=agent.title or None,
            model_path=str(model_path),
            provenance="Modeler agent synthesized the MJCF after automatic resolution missed",
            processing_steps=["Modeler synthesis", "MJCF validation"],
            approximate=True,
        )
        return Stage.BUILD_HARNESS

    async def stage_build_harness(self) -> Stage:
        """Bind the pushed control code to the simulated robot.

        Dispatches the Harness Builder role: it writes the adapter that makes
        the customer's ``control.entrypoint`` drive MuJoCo actuators instead of
        a real driver. Accepted only once a smoke scenario executes.
        """
        from simkit import runner

        ctx = self.ctx
        control = ctx.config.get("control", {})
        harness_path = self._artifacts / HARNESS_FILENAME
        await HarnessBuilderAgent(ctx).dispatch(
            entrypoint=control.get("entrypoint", ""),
            interface=control.get("interface", ""),
            rate_hz=control.get("rate_hz", 100),
            model_path=self._model_path(),
            harness_out_path=str(harness_path),
        )
        if not harness_path.exists():
            raise PipelineError(f"Harness Builder wrote no harness at {harness_path}")

        # Prove the harness by executing it once. An `error` here is ours.
        smoke = await asyncio.to_thread(
            runner.run_scenario,
            scenario_id="smoke",
            model_path=self._model_path(),
            harness_path=str(harness_path),
            params={},
            seed=self._base_seed(),
            task=ctx.config.get("task", {}),
            record=False,
        )
        if smoke.status == "error":
            raise PipelineError(f"harness smoke test errored: {smoke.error}")
        return Stage.DESIGN_SCENARIOS

    async def stage_design_scenarios(self) -> Stage:
        """Design the randomized world matrix.

        Dispatches the Scenario Designer role to choose *which* axes to
        randomize and over what ranges; the concrete sampling is done
        deterministically by :mod:`simkit.scenarios` from a seed.
        """
        from simkit import scenarios as scenario_gen

        ctx = self.ctx
        task = ctx.config.get("task", {})
        configured_size = ctx.config.get("scenarios", {}).get("count")
        suite_size = int(
            ctx.suite_size
            if ctx.suite_size is not None
            else (
                configured_size
                if configured_size is not None
                else ctx.default_suite_size
            )
        )
        agent = await ScenarioDesignerAgent(ctx).dispatch(
            task_description=task.get("description", task.get("name", "")),
            success_criteria=task.get("success", []),
            suite_size=suite_size,
        )
        axes = self._axes(agent)
        if not axes:
            raise PipelineError(
                "no randomization axes: neither robotci.yaml scenarios."
                "randomize nor the Scenario Designer produced any"
            )

        raw = await asyncio.to_thread(
            scenario_gen.generate,
            ctx.run.id,
            self._base_seed(),
            suite_size,
            axes,
        )
        ctx.scenarios = [
            self._to_scenario(index, spec) for index, spec in enumerate(raw)
        ]
        # The whole matrix up front, so the dashboard renders the full grid
        # greyed out instead of growing it cell by cell.
        for scenario in ctx.scenarios:
            await ctx.bus.emit(
                ctx.run.id,
                EventType.SCENARIO_CREATED,
                scenario.model_dump(mode="json"),
            )
        return Stage.RUN_SUITE

    async def stage_run_suite(self) -> Stage:
        """Execute every scenario in parallel. The oracle speaks here.

        No agents involved. Emits ``suite.progress`` as cells complete so the
        matrix fills in live. Returns ``PASSED_CLEAN`` when nothing failed.
        """
        results = await self._execute_suite(self.ctx.scenarios)
        self._before_results = results
        stats = self._apply_results(self.ctx.scenarios, results)
        self.ctx.run.suite = stats
        if stats.failed == 0:
            return Stage.PASSED_CLEAN
        return Stage.CLUSTER_FAILURES

    async def stage_cluster_failures(self) -> Stage:
        """Group failing scenarios by suspected shared cause.

        Cheap and deterministic — clustering on diagnosis text, not an agent.
        Cluster count sets the Investigator fan-out width, so it directly
        controls how many Devin sessions we spend.
        """
        ctx = self.ctx
        ctx.clusters = clustering.cluster_failures(
            ctx.run.id, ctx.scenarios, max_clusters=self._max_parallel
        )
        if not ctx.clusters:
            return Stage.PASSED_CLEAN
        by_id = {s.id: s for s in ctx.scenarios}
        for cluster in ctx.clusters:
            original_seeds = [
                by_id[scenario_id].seed
                for scenario_id in cluster.scenario_ids
                if scenario_id in by_id
            ]
            for scenario_id in cluster.scenario_ids:
                by_id[scenario_id].cluster_id = cluster.id
            self._cluster_work[cluster.id] = _ClusterWork(
                cluster=cluster,
                original_seeds=original_seeds,
            )
        return Stage.INVESTIGATE

    async def stage_investigate(self) -> Stage:
        """Start independent cluster workflows and wait for the first cause.

        Cluster phases advance independently; the global stage is the maximum
        phase reached by any cluster and never moves backwards. This is why a
        later cluster can enter FIX while the run already reads VERIFY.
        """
        self._start_cluster_workflows()
        if await self._wait_for_cluster(
            lambda work: (
                work.phase
                in {"root_cause", "fixing", "ready_to_verify", "verifying", "resolved"}
            )
        ):
            return Stage.FIX
        self.ctx.run.error = "no root cause was established for any cluster"
        return Stage.FAILED_UNRESOLVED

    async def stage_fix(self) -> Stage:
        """Let workflows continue until one patch is ready to verify."""
        if await self._wait_for_cluster(
            lambda work: work.phase in {"ready_to_verify", "verifying", "resolved"}
        ):
            return Stage.VERIFY
        self.ctx.run.error = "every Fixer failed before producing a patch"
        return Stage.FAILED_UNRESOLVED

    async def stage_verify(self) -> Stage:
        """Re-run the FULL suite against all accepted patches together.

        This is the gate that catches fixes which break other scenarios — the
        single most important stage, because independent agents patching the
        same repo will otherwise conflict. Loops back to FIX while the budget
        holds; the Reviewer role adjudicates conflicts and dedupes patches.
        """
        from simkit import suite as suite_mod

        ctx = self.ctx
        await self._wait_for_all_clusters()
        await self._retry_conflicted_clusters()
        await asyncio.gather(*self._cluster_tasks.values(), return_exceptions=True)
        verify_tree = ctx.workspace.worktree("verify")

        results = await self._execute_suite(ctx.scenarios, repo_dir=verify_tree)
        self._after_results = results
        after = self._apply_results(ctx.scenarios, results)
        before = ctx.run.suite or after
        comparison = await asyncio.to_thread(
            suite_mod.compare, self._before_results, results
        )

        await ReviewerAgent(ctx).dispatch(
            fix_summary=[f.summary for f in ctx.blackboard.confirmed_root_causes()],
            before_stats=before.model_dump(mode="json"),
            after_stats=after.model_dump(mode="json"),
            regressions=comparison.get("newly_broken", []),
            conflicts=[_conflict_description(conflict) for conflict in self._conflicts],
            diff=await workspace_mod.diff(ctx.workspace, "verify"),
        )

        clean = after.failed == 0 and not comparison.get("newly_broken")
        if clean and not self._conflicts:
            return Stage.REPORT
        ctx.fix_iteration += 1
        if ctx.fix_iteration < ctx.max_fix_iterations:
            # The Reviewer's notes are on the board; the next FIX round reads
            # them through the relay policy.
            return Stage.FIX
        ctx.run.error = self._verification_failure_reason(after, comparison)
        # REPORT is still required on an unsuccessful run so humans receive
        # the incident summary, but stage_report will not push a branch.
        return Stage.REPORT

    def _start_cluster_workflows(self) -> None:
        """Start one never-cancelling workflow per cluster."""
        if self._cluster_tasks:
            return
        for cluster_id in self._cluster_work:
            task = asyncio.create_task(self._run_cluster(cluster_id))
            self._cluster_tasks[cluster_id] = task

    async def _run_cluster(self, cluster_id: str) -> None:
        work = self._cluster_work[cluster_id]
        try:
            await self._investigate_cluster(work)
            if work.phase == "root_cause":
                await self._fix_cluster(work)
            if work.phase == "ready_to_verify":
                await self._verify_cluster(work)
        except Exception as exc:  # noqa: BLE001 - isolate one cluster
            work.error = f"{type(exc).__name__}: {exc}"
            work.outcome = "unresolved"
            work.phase = "unresolved"
            self._cluster_progress.set()
            await self._nonfatal(f"cluster {work.cluster.label}", exc)

    async def _investigate_cluster(self, work: _ClusterWork) -> None:
        """Run one Investigator while other clusters continue independently."""
        ctx = self.ctx
        scenarios = [
            scenario
            for scenario in ctx.scenarios
            if scenario.id in set(work.cluster.scenario_ids)
        ]
        self._set_cluster_phase(work, "investigating")
        gate = self._agent_gate
        role = InvestigatorAgent(ctx)
        async with gate:
            agent = await self._dispatch_with_agent_watch(
                role,
                issue=_cluster_issue(scenarios),
                step="investigating",
                cluster_id=work.cluster.id,
                cluster_label=work.cluster.label,
                cluster_size=work.cluster.size,
                scenario_seeds=work.original_seeds,
                diagnoses=[s.diagnosis or "" for s in scenarios],
                param_correlation=clustering.correlate_params(scenarios),
            )
        work.agent_ids.append(agent.id)
        work.owner_agent_id = agent.id
        self._agent_tree.register_root(agent.id)
        for finding in self._findings_of(agent):
            if finding.kind is FindingKind.OBSERVATION:
                await role.relay(finding, Role.INVESTIGATOR, "finding")
        cause = next(
            (
                finding
                for finding in reversed(ctx.blackboard.for_cluster(work.cluster.id))
                if finding.kind is FindingKind.ROOT_CAUSE
                and finding.confidence >= CONFIRM_CONFIDENCE
            ),
            None,
        )
        if cause is None:
            work.outcome = "unresolved"
            self._set_cluster_phase(work, "unresolved")
            return
        work.cause = cause
        await ctx.blackboard.confirm(cause.id, cause.author_agent_id or agent.id)
        self._set_cluster_phase(work, "root_cause")

    async def _fix_cluster(self, work: _ClusterWork) -> None:
        """Patch one cluster in isolation, bounded only while the agent runs."""
        if work.cause is None:
            self._set_cluster_phase(work, "unresolved")
            work.outcome = "unresolved"
            return
        ctx = self.ctx
        if work.owner_agent_id is None:
            work.owner_agent_id = work.cause.author_agent_id
        if work.owner_agent_id and not self._agent_tree.has(work.owner_agent_id):
            self._agent_tree.register_root(work.owner_agent_id)
        parent_id = work.owner_agent_id
        refusal = (
            self._agent_tree.child_refusal(parent_id)
            if parent_id is not None
            else "agent tree parent unavailable for fixer"
        )
        if refusal is not None:
            await self._refuse_cluster(work, refusal)
            return
        name = f"fix-{work.cluster.id}"
        branch = f"robotci/{name}-{ctx.run.commit_sha[:7]}"
        path = await workspace_mod.create_worktree(ctx.workspace, name, branch)
        self._set_cluster_phase(work, "fixing")
        gate = self._agent_gate
        role = FixerAgent(ctx)
        async with gate:
            agent = await self._dispatch_with_agent_watch(
                role,
                issue=work.cause.summary,
                step="fixing",
                root_cause=work.cause.summary,
                finding_id=work.cause.id,
                cluster_id=work.cluster.id,
                files=work.cause.files,
                worktree=str(path),
                scenario_seeds=work.original_seeds,
                iteration=ctx.fix_iteration,
                parent_agent_id=parent_id,
            )
        work.agent_ids.append(agent.id)
        self._agent_tree.register_child(parent_id, agent.id)
        if "patched" in role.output and not role.output["patched"]:
            work.outcome = "unresolved"
            self._set_cluster_phase(work, "unresolved")
            return
        cluster_scenarios = self._cluster_scenarios(work)
        seed_results = await self._execute_cluster_suite(
            cluster_scenarios, repo_dir=path
        )
        results_by_seed = {
            getattr(result, "seed", None): result for result in seed_results
        }
        still_red = [
            str(scenario.seed)
            for scenario in cluster_scenarios
            if (
                results_by_seed.get(scenario.seed) is None
                or results_by_seed[scenario.seed].status != "passed"
            )
        ]
        reviewer_error: str | None = None
        reviewer = ReviewerAgent(ctx)
        refusal = self._agent_tree.child_refusal(agent.id)
        if refusal is not None:
            await self._record_refusal(work, refusal)
            reviewer_error = refusal
        else:
            try:
                patch_diff = await workspace_mod.diff(ctx.workspace, name)
            except Exception as exc:  # noqa: BLE001 - evidence is best effort
                patch_diff = "(diff unavailable)"
                await self._nonfatal(f"cluster {work.cluster.label} patch diff", exc)
            try:
                async with gate:
                    reviewer_agent = await self._dispatch_with_agent_watch(
                        reviewer,
                        issue=work.cause.summary,
                        step="verifying",
                        root_cause=work.cause.summary,
                        cluster_id=work.cluster.id,
                        cluster_label=work.cluster.label,
                        fix_summary=[work.cause.summary],
                        before_stats=self._stats(cluster_scenarios),
                        after_stats=self._result_stats(seed_results),
                        regressions=still_red,
                        conflicts=[
                            _conflict_description(conflict)
                            for conflict in self._conflicts
                        ],
                        diff=patch_diff,
                        parent_agent_id=agent.id,
                    )
                work.agent_ids.append(reviewer_agent.id)
                self._agent_tree.register_child(agent.id, reviewer_agent.id)
                reviewer_claimed_success = reviewer.output.get("verdict") == "ship"
                if reviewer_claimed_success and still_red:
                    reviewer_error = (
                        "Reviewer claimed success while originally red seeds stayed "
                        f"red: {', '.join(still_red)}"
                    )
            except Exception as exc:  # noqa: BLE001 - reviewer telemetry is optional
                reviewer_error = f"Reviewer unavailable: {exc}"
                await self._nonfatal(f"cluster {work.cluster.label} reviewer", exc)
        if still_red:
            work.error = (
                "originally red seeds still failing in fixer worktree: "
                + ", ".join(still_red)
            )
            if reviewer_error:
                work.error += f"; {reviewer_error}"
            work.outcome = "unresolved"
            self._set_cluster_phase(work, "unresolved")
            return
        if reviewer_error:
            work.error = reviewer_error
        work.worktree = name
        self._fix_worktrees.append(name)
        self._set_cluster_phase(work, "ready_to_verify")

    async def _verify_cluster(self, work: _ClusterWork) -> None:
        """Serialize merge plus exact-seed verification for one cluster."""
        if work.worktree is None:
            return
        async with self._merge_lock:
            self._set_cluster_phase(work, "verifying")
            conflicts = await workspace_mod.merge_patches(
                self.ctx.workspace,
                [work.worktree],
                into="verify",
                landed_worktrees=self._landed_worktrees,
            )
            if conflicts:
                self._conflicts.extend(
                    conflict
                    for conflict in conflicts
                    if conflict not in self._conflicts
                )
                work.outcome = "unresolved"
                self._set_cluster_phase(work, "conflicted")
                return
            if work.worktree not in self._landed_worktrees:
                self._landed_worktrees.append(work.worktree)
            results = await self._execute_cluster_suite(
                self._cluster_scenarios(work),
                repo_dir=self.ctx.workspace.worktree("verify"),
            )
            if results and all(result.status == "passed" for result in results):
                await self._record_cluster_after(work)
                self._conflicts = [
                    conflict
                    for conflict in self._conflicts
                    if not _conflict_worktree(conflict, work.worktree)
                ]
                work.outcome = "resolved"
                self._set_cluster_phase(work, "resolved")
            else:
                work.outcome = "unresolved"
                self._set_cluster_phase(work, "unresolved")

    async def _record_cluster_after(self, work: _ClusterWork) -> None:
        """Record passing cluster seeds without changing authoritative records."""
        ctx = self.ctx
        scenarios = self._cluster_scenarios(work)
        # A real cluster verification always creates the shared pool. Keeping
        # this seam inert for pool-less unit fakes avoids turning a mocked
        # verification into an unrelated MuJoCo execution.
        if not scenarios or self._pool is None:
            return
        self._after_videos.pop(work.cluster.id, None)
        record_dir = self._artifacts / "video" / "after"
        results = await self._pool.submit(
            [self._suite_spec(scenario) for scenario in scenarios],
            model_path=self._model_path(),
            harness_path=str(self._artifacts / HARNESS_FILENAME),
            task=ctx.config.get("task", {}),
            record="all",
            record_dir=record_dir,
            repo_dir=ctx.workspace.worktree("verify"),
            reason=f"record after evidence: {work.cluster.label}",
        )
        videos: dict[str, str] = {}
        before_paths = {
            Path(scenario.video_path).resolve()
            for scenario in scenarios
            if scenario.video_path
        }
        for scenario, result in zip(scenarios, results):
            path = getattr(result, "video_path", None)
            if (
                getattr(result, "status", None) == "passed"
                and path
                and Path(path).is_file()
                and Path(path).resolve() not in before_paths
            ):
                videos[scenario.id] = str(path)
        if videos:
            self._after_videos[work.cluster.id] = videos

    async def _retry_conflicted_clusters(self) -> None:
        """Retry each conflict once after all non-conflicting patches landed."""
        for work in self._cluster_work.values():
            if work.phase != "conflicted" or work.worktree is None:
                continue
            work.retry_count += 1
            await self._verify_cluster(work)
            if work.phase == "conflicted":
                work.outcome = "unresolved"
                self._set_cluster_phase(work, "unresolved")

    def _cluster_scenarios(self, work: _ClusterWork) -> list[Scenario]:
        by_id = {scenario.id: scenario for scenario in self.ctx.scenarios}
        remaining = list(work.cluster.scenario_ids)
        scenarios: list[Scenario] = []
        for seed in work.original_seeds:
            for scenario_id in remaining:
                scenario = by_id.get(scenario_id)
                if scenario is not None and scenario.seed == seed:
                    scenarios.append(scenario)
                    remaining.remove(scenario_id)
                    break
        return scenarios

    def _set_cluster_phase(self, work: _ClusterWork, phase: str) -> None:
        work.phase = phase
        self._cluster_progress.set()

    async def _refuse_cluster(self, work: _ClusterWork, reason: str) -> None:
        """Record a cap refusal as an honest, non-fatal cluster failure."""
        await self._record_refusal(work, reason)
        work.outcome = "unresolved"
        self._set_cluster_phase(work, "unresolved")

    async def _record_refusal(self, work: _ClusterWork, reason: str) -> None:
        """Record a cap refusal without deciding the cluster outcome."""
        work.error = reason
        await self.ctx.bus.emit(
            self.ctx.run.id,
            EventType.ERROR,
            {
                "stage": self.ctx.run.stage.value,
                "message": f"cluster {work.cluster.label}: {reason}",
                "fatal": False,
            },
        )

    @staticmethod
    def _result_stats(results: list[Any]) -> SuiteStats:
        """Summarize private oracle evidence without mutating scenarios."""
        passed = sum(1 for result in results if result.status == "passed")
        failed = len(results) - passed
        return SuiteStats.from_counts(passed=passed, failed=failed)

    async def _wait_for_cluster(
        self, predicate: Callable[[_ClusterWork], bool]
    ) -> bool:
        """Wait for a milestone without imposing a batch barrier."""
        while True:
            if any(predicate(work) for work in self._cluster_work.values()):
                return True
            if self._cluster_work and all(
                work.phase in _CLUSTER_TERMINAL for work in self._cluster_work.values()
            ):
                return False
            self._cluster_progress.clear()
            await self._cluster_progress.wait()

    async def _wait_for_all_clusters(self) -> None:
        """Wait until every independent workflow has reached a terminal phase."""
        while self._cluster_work and not all(
            work.phase in _CLUSTER_TERMINAL for work in self._cluster_work.values()
        ):
            self._cluster_progress.clear()
            await self._cluster_progress.wait()

    async def _dispatch_with_agent_watch(
        self,
        role: RoleAgent,
        *,
        issue: str,
        step: str,
        **kwargs: Any,
    ) -> Agent:
        """Dispatch a role while streaming its public session fields."""
        finished = asyncio.Event()
        watcher = asyncio.create_task(
            self._watch_agent(role, issue=issue, step=step, finished=finished)
        )
        try:
            return await role.dispatch(**kwargs)
        finally:
            finished.set()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            await self._safe_emit_agent_update(role, issue=issue, step=step)

    async def _watch_agent(
        self,
        role: RoleAgent,
        *,
        issue: str,
        step: str,
        finished: asyncio.Event,
    ) -> None:
        """Poll public session state without coupling it to dispatch success."""
        while not finished.is_set():
            await self._safe_emit_agent_update(role, issue=issue, step=step)
            try:
                await asyncio.wait_for(finished.wait(), timeout=_AGENT_WATCH_INTERVAL_S)
            except TimeoutError:
                continue

    async def _safe_emit_agent_update(
        self,
        role: RoleAgent,
        *,
        issue: str,
        step: str,
    ) -> None:
        try:
            await self._emit_agent_update(role, issue=issue, step=step)
        except Exception as exc:  # noqa: BLE001 - telemetry is best effort
            log.warning("agent update failed: %s", exc)

    async def _emit_agent_update(
        self,
        role: InvestigatorAgent | FixerAgent,
        *,
        issue: str,
        step: str,
    ) -> None:
        """Publish only newly known agent fields, never a full Agent object."""
        session = role.session
        if session is None:
            return
        agent = session.agent
        handle = getattr(session, "handle", None)
        previous = self._agent_updates.setdefault(agent.id, {})
        agent_step = getattr(agent, "step", None)
        step_value = (
            agent_step if agent_step and agent_step != previous.get("step") else step
        )
        values = {
            "session_url": getattr(agent, "session_url", None),
            "desktop_url": _optional_field(handle, "desktop_url")
            or getattr(agent, "desktop_url", None),
            "issue": issue or None,
            "step": step_value or None,
        }
        patch: dict[str, Any] = {"agent_id": agent.id}
        for name, value in values.items():
            if value is None or previous.get(name) == value:
                continue
            setattr(agent, name, value)
            previous[name] = value
            patch[name] = value
        if len(patch) > 1:
            await self.ctx.bus.emit(self.ctx.run.id, EventType.AGENT_UPDATED, patch)

    async def stage_report(self) -> Stage:
        """Write the incident report from the confirmed blackboard findings."""
        ctx = self.ctx
        after = self._stats(ctx.scenarios)
        before = ctx.run.suite or after
        diff = await workspace_mod.diff(ctx.workspace, "verify")
        incidents = [self._incident(cluster) for cluster in ctx.clusters]

        await ReporterAgent(ctx).dispatch(
            confirmed_findings=[
                f.model_dump(mode="json")
                for f in ctx.blackboard.for_role(Role.REPORTER)
            ],
            before_stats=before.model_dump(mode="json"),
            after_stats=after.model_dump(mode="json"),
            diff=diff,
            video_pairs=self._video_pairs(incidents),
        )

        verdict = (
            Verdict.FIXED if self._verification_is_green(after) else Verdict.UNRESOLVED
        )
        report = Report(
            run_id=ctx.run.id,
            verdict=verdict,
            title=self._report_title(before, after),
            summary=self._report_summary(before, after, incidents),
            incidents=incidents,
            diff=diff,
            before=before,
            after=after,
        )
        self._artifacts.mkdir(parents=True, exist_ok=True)
        markdown = self._artifacts / "report.md"
        markdown.write_text(github.render_pr_body(report))
        report.markdown_path = str(markdown)

        ctx.report = report
        ctx.run.report_id = report.id
        await ctx.bus.emit(
            ctx.run.id, EventType.REPORT_CREATED, report.model_dump(mode="json")
        )
        if verdict is Verdict.UNRESOLVED:
            if not ctx.run.error:
                ctx.run.error = self._verification_failure_reason(
                    after, {"newly_broken": []}
                )
            body = github.render_pr_body(report)
            await github.comment_on_commit(ctx.run.repo, ctx.run.commit_sha, body)
            await github.set_commit_status(
                ctx.run.repo,
                ctx.run.commit_sha,
                "failure",
                f"Robot CI unresolved: {ctx.run.error}",
                target_url=ctx.run.pull_request_url,
            )
            return Stage.FAILED_UNRESOLVED
        return Stage.PR_OPENED

    async def stage_pr_opened(self) -> Stage:
        """Push the branch and open the pull request. Terminal."""
        ctx = self.ctx
        report = ctx.report
        if report is None:
            raise PipelineError("cannot open a pull request without a report")

        branch = github.branch_name(ctx.run)
        await github.push_branch(
            str(ctx.workspace.worktree("verify")),
            branch,
            f"{report.title}\n\nRobot CI verified this against the full "
            f"scenario suite. See {report.markdown_path or 'the report'}.",
        )
        body = github.render_pr_body(report)
        if ctx.config.get("policy", {}).get("open_pull_request", True):
            url = await github.open_pull_request(
                ctx.run.repo,
                branch,
                os.getenv("TARGET_BRANCH", ctx.run.branch),
                report,
            )
            report.pull_request_url = url
            ctx.run.pull_request_url = url
        else:
            # PRs disabled: the report still has to reach a human.
            await github.comment_on_commit(ctx.run.repo, ctx.run.commit_sha, body)

        state = "success" if report.verdict is Verdict.FIXED else "failure"
        after = report.after
        await github.set_commit_status(
            ctx.run.repo,
            ctx.run.commit_sha,
            state,
            f"Robot CI: {after.passed}/{after.total} scenarios pass"
            if after
            else "Robot CI finished",
            target_url=ctx.run.pull_request_url,
        )
        return Stage.PR_OPENED

    # -- suite execution --------------------------------------------------- #

    async def _execute_suite(
        self, scenarios: list[Scenario], repo_dir: Path | None = None
    ) -> list[Any]:
        """Submit scenarios to the run's shared pool and stream full records."""
        ctx = self.ctx
        if self._pool is None:
            self._pool = SuitePool(
                run_id=ctx.run.id,
                bus=ctx.bus,
                workers=self._worker_count(),
                artifacts_dir=self._artifacts,
            )
        policy = ctx.config.get("policy", {})
        max_wall_s = float(
            policy.get("scenario_timeout_s", os.getenv("SCENARIO_TIMEOUT_S", "60"))
        )
        completed = 0
        passed = 0
        failed = 0
        by_id = {scenario.id: scenario for scenario in scenarios}

        async def on_started(scenario_id: str, worker_id: str, _attempt: int) -> None:
            scenario = by_id.get(scenario_id)
            if scenario is None:
                return
            scenario.status = ScenarioStatus.RUNNING
            scenario.worker_id = worker_id

        async def on_result(result: Any) -> None:
            nonlocal completed, passed, failed
            scenario = by_id.get(result.scenario_id)
            if scenario is None:
                return
            self._apply_result(scenario, result)
            completed += 1
            if result.status == "passed":
                passed += 1
            elif result.status in ("failed", "error"):
                failed += 1
            await ctx.bus.emit(
                ctx.run.id,
                EventType.SCENARIO_FINISHED,
                scenario.model_dump(mode="json"),
            )
            if completed % PROGRESS_EVERY == 0:
                snapshot = self._pool.snapshot()
                await ctx.bus.emit(
                    ctx.run.id,
                    EventType.SUITE_PROGRESS,
                    {
                        "total": len(scenarios),
                        "completed": completed,
                        "passed": passed,
                        "failed": failed,
                        "running": snapshot["busy"],
                        "queued": snapshot["queued"],
                        "workers": snapshot["workers"],
                    },
                )

        return await self._pool.submit(
            [self._suite_spec(scenario) for scenario in scenarios],
            model_path=self._model_path(),
            harness_path=str(self._artifacts / HARNESS_FILENAME),
            task=ctx.config.get("task", {}),
            record=ctx.config.get("policy", {}).get("record_video", "failures"),
            repo_dir=repo_dir,
            on_result=on_result,
            on_started=on_started,
            reason=f"{ctx.run.stage.value.lower()}: {len(scenarios)} scenarios",
            max_wall_s=max_wall_s,
        )

    async def _execute_cluster_suite(
        self, scenarios: list[Scenario], repo_dir: Path
    ) -> list[Any]:
        """Verify cluster seeds through the pool without changing run records.

        Cluster checks are private evidence for deciding whether one patch is
        safe. The authoritative Scenario records and suite progress belong to
        the original suite and final full-suite verification only.
        """
        ctx = self.ctx
        if self._pool is None:
            self._pool = SuitePool(
                run_id=ctx.run.id,
                bus=ctx.bus,
                workers=self._worker_count(),
                artifacts_dir=self._artifacts,
            )
        policy = ctx.config.get("policy", {})
        max_wall_s = float(
            policy.get("scenario_timeout_s", os.getenv("SCENARIO_TIMEOUT_S", "60"))
        )
        return await self._pool.submit(
            [self._suite_spec(scenario) for scenario in scenarios],
            model_path=self._model_path(),
            harness_path=str(self._artifacts / HARNESS_FILENAME),
            task=ctx.config.get("task", {}),
            record="none",
            repo_dir=repo_dir,
            max_wall_s=max_wall_s,
        )

    def _apply_results(
        self, scenarios: list[Scenario], results: list[Any]
    ) -> SuiteStats:
        """Fold simkit results back onto the Scenario records."""
        by_id = {s.id: s for s in scenarios}
        for result in results:
            scenario = by_id.get(result.scenario_id)
            if scenario is None:
                continue
            self._apply_result(scenario, result)
        return self._stats(scenarios)

    @staticmethod
    def _apply_result(scenario: Scenario, result: Any) -> None:
        """Fold one oracle result onto its live protocol record."""
        scenario.status = ScenarioStatus(result.status)
        scenario.duration_s = result.duration_s
        scenario.sim_time_s = result.sim_time_s
        scenario.diagnosis = result.diagnosis
        scenario.video_path = result.video_path
        scenario.trace_path = result.trace_path
        scenario.error = result.error
        scenario.error_kind = getattr(result, "error_kind", None)
        scenario.retries = int(getattr(result, "retries", 0) or 0)
        scenario.retry_reason = getattr(result, "retry_reason", None)
        scenario.worker_id = getattr(result, "worker_id", scenario.worker_id)
        scenario.criteria = [
            CriterionResult(**criterion) for criterion in result.criteria
        ]

    @staticmethod
    def _stats(scenarios: list[Scenario]) -> SuiteStats:
        passed = sum(1 for s in scenarios if s.status is ScenarioStatus.PASSED)
        failed = sum(
            1
            for s in scenarios
            if s.status in (ScenarioStatus.FAILED, ScenarioStatus.ERROR)
        )
        return SuiteStats.from_counts(passed=passed, failed=failed)

    @staticmethod
    def _suite_spec(scenario: Scenario) -> dict[str, Any]:
        return {
            "id": scenario.id,
            "index": scenario.index,
            "seed": scenario.seed,
            "label": scenario.label,
            "params": scenario.params,
        }

    def _to_scenario(self, index: int, spec: dict[str, Any]) -> Scenario:
        fields = set(Scenario.model_fields)
        payload = {k: v for k, v in spec.items() if k in fields}
        payload.setdefault("index", index)
        payload["run_id"] = self.ctx.run.id
        return Scenario(**payload)

    # -- report helpers ---------------------------------------------------- #

    def _incident(self, cluster: Cluster) -> Incident:
        ctx = self.ctx
        findings = ctx.blackboard.for_cluster(cluster.id)
        cause = next(
            (f for f in findings if f.kind is FindingKind.ROOT_CAUSE),
            None,
        )
        patch = next((f for f in findings if f.kind is FindingKind.PATCH), None)
        scenarios = [s for s in ctx.scenarios if s.cluster_id == cluster.id]
        still_failing = any(
            s.status in (ScenarioStatus.FAILED, ScenarioStatus.ERROR) for s in scenarios
        )
        before_video = next(
            (
                s.video_path
                for s in scenarios
                if s.video_path and Path(s.video_path).is_file()
            ),
            None,
        )
        after_video = next(
            (
                path
                for scenario_id, path in self._after_videos.get(cluster.id, {}).items()
                if scenario_id in {s.id for s in scenarios} and Path(path).is_file()
            ),
            None,
        )
        return Incident(
            cluster_id=cluster.id,
            title=cluster.label,
            affected_scenarios=cluster.size,
            root_cause=cause.summary if cause else "not established",
            resolution=patch.summary if patch else "no patch accepted",
            files_changed=patch.files if patch else [],
            before_video=before_video,
            after_video=after_video,
            status=("unresolved" if still_failing or patch is None else "fixed"),
        )

    def _video_pairs(
        self, incidents: list[Incident] | None = None
    ) -> list[dict[str, Any]]:
        if incidents is None:
            incidents = [self._incident(cluster) for cluster in self.ctx.clusters]
        labels = {cluster.id: cluster.label for cluster in self.ctx.clusters}
        return [
            {
                "cluster_id": incident.cluster_id,
                "label": labels.get(incident.cluster_id, incident.title),
                "before": incident.before_video,
                "after": incident.after_video,
                "after_note": (
                    "verified after-video"
                    if incident.after_video
                    else "no verified after-video; proof is unavailable"
                ),
            }
            for incident in incidents
        ]

    def _verification_is_green(self, after: SuiteStats) -> bool:
        """Return whether the current full-suite verification may ship."""
        if after.failed != 0 or self._conflicts:
            return False
        if self._before_results and self._after_results:
            from simkit import suite as suite_mod

            comparison = suite_mod.compare(self._before_results, self._after_results)
            return not comparison.get("newly_broken")
        return True

    def _verification_failure_reason(
        self, after: SuiteStats, comparison: dict[str, Any]
    ) -> str:
        reasons: list[str] = []
        if after.failed:
            reasons.append(f"{after.failed} scenarios still failing")
        regressions = comparison.get("newly_broken", [])
        if regressions:
            reasons.append(f"newly broken seeds: {', '.join(map(str, regressions))}")
        if self._conflicts:
            reasons.append(
                "unresolved patch conflicts: "
                + "; ".join(
                    _conflict_description(conflict) for conflict in self._conflicts
                )
            )
        cluster_errors = [
            f"{work.cluster.label}: {work.error}"
            for work in self._cluster_work.values()
            if work.error and work.outcome != "resolved"
        ]
        if cluster_errors:
            reasons.append("cluster errors: " + "; ".join(cluster_errors))
        return "verification unresolved: " + (
            "; ".join(reasons) if reasons else "suite did not pass"
        )

    def _report_title(self, before: SuiteStats, after: SuiteStats) -> str:
        fixed = after.passed - before.passed
        if after.failed == 0:
            return (
                f"Fix {len(self.ctx.clusters)} simulated failure(s) in the robot task"
            )
        return f"Fix {max(fixed, 0)} of {before.failed} simulated failures"

    def _report_summary(
        self, before: SuiteStats, after: SuiteStats, incidents: list[Incident]
    ) -> str:
        unresolved = [i for i in incidents if i.status == "unresolved"]
        lines = [
            (
                f"Robot CI simulated `{self.ctx.run.commit_sha[:7]}` across "
                f"{before.total} randomized scenarios: {before.failed} failed."
            ),
            "",
            (
                f"After {len(incidents) - len(unresolved)} accepted fix(es), "
                f"{after.passed}/{after.total} scenarios pass."
            ),
        ]
        if unresolved:
            lines += [
                "",
                "Still failing: "
                + ", ".join(f"{i.title} ({i.affected_scenarios})" for i in unresolved),
            ]
        if self._conflicts:
            lines += [
                "",
                "Conflicting patches: "
                + "; ".join(
                    _conflict_description(conflict) for conflict in self._conflicts
                ),
            ]
        return "\n".join(lines)

    # -- misc helpers ------------------------------------------------------ #

    def _model_path(self) -> str:
        model = self.ctx.run.robot_model
        if model is None:
            raise PipelineError("no robot model resolved")
        return model.model_path

    def _base_seed(self) -> int:
        return int(self.ctx.config.get("scenarios", {}).get("seed", 1337))

    def _axes(self, agent: Agent) -> dict[str, tuple[float, float]]:
        """Randomization axes: the Designer's, else ``robotci.yaml``.

        The Designer publishes them as a JSON object in an ``observation``
        finding's ``detail`` — the role API hands the pipeline an ``Agent``, not
        the session's structured output, so the board is the only channel.
        """
        axes: dict[str, tuple[float, float]] = {}
        for finding in reversed(self._findings_of(agent)):
            axes = _parse_axes(finding.detail)
            if axes:
                break
        if not axes:
            configured = self.ctx.config.get("scenarios", {}).get("randomize", {})
            axes = {
                name: (float(bounds[0]), float(bounds[1]))
                for name, bounds in configured.items()
                if isinstance(bounds, (list, tuple)) and len(bounds) == 2
            }
        if len(axes) < 3:
            from simkit.scenarios import DEFAULT_AXES

            for name, bounds in DEFAULT_AXES.items():
                if len(axes) >= 3:
                    break
                axes.setdefault(name, bounds)
        return axes

    def _worker_count(self) -> int:
        """Resolve the shared simulation-worker count once for every suite."""
        configured = (
            self.ctx.sim_workers
            if self.ctx.sim_workers is not None
            else int(os.getenv("SIM_WORKERS", "4"))
        )
        return max(1, min(int(configured), os.cpu_count() or 1))

    def _findings_of(self, agent: Agent) -> list[Finding]:
        ids = set(agent.finding_ids)
        return [f for f in self.ctx.blackboard.all() if f.id in ids]

    async def _nonfatal(self, what: str, exc: BaseException) -> None:
        """Report an agent-level failure without killing the run."""
        log.warning("%s failed: %s", what, exc)
        await self.ctx.bus.emit(
            self.ctx.run.id,
            EventType.ERROR,
            {
                "stage": self.ctx.run.stage.value,
                "message": f"{what} failed: {exc}",
                "fatal": False,
            },
        )

    async def _force_failed(self) -> None:
        run = self.ctx.run
        if run.stage is Stage.FAILED_UNRESOLVED:
            return
        if can_transition(run.stage, Stage.FAILED_UNRESOLVED):
            await self.advance(Stage.FAILED_UNRESOLVED)
            return
        previous = run.stage
        run.stage = Stage.FAILED_UNRESOLVED
        await self.ctx.bus.emit(
            run.id,
            EventType.RUN_STAGE_CHANGED,
            {"stage": run.stage.value, "previous_stage": previous.value},
        )

    async def _finish(self) -> None:
        run = self.ctx.run
        if self._pool is not None:
            await self._pool.aclose()
        run.finished_at = _now()
        run.updated_at = run.finished_at
        await self.ctx.bus.emit(
            run.id, EventType.RUN_FINISHED, run.model_dump(mode="json")
        )
        await self.ctx.bus.close(run.id)
        if run.stage is not Stage.FAILED_UNRESOLVED:
            # Keep the checkout on a failed run: it is the only way to see what
            # the agents were looking at.
            await workspace_mod.cleanup(self.ctx.workspace, keep_artifacts=True)


def _parse_axes(detail: str) -> dict[str, tuple[float, float]]:
    """Pull ``{"friction": [0.2, 0.9]}`` out of a finding's detail text."""
    start = detail.find("{")
    end = detail.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(detail[start : end + 1])
    except json.JSONDecodeError:
        return {}
    axes: dict[str, tuple[float, float]] = {}
    for name, bounds in payload.items() if isinstance(payload, dict) else []:
        if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
            try:
                axes[name] = (float(bounds[0]), float(bounds[1]))
            except (TypeError, ValueError):
                continue
    return axes


def _optional_field(value: Any, name: str) -> Any:
    """Read an optional field from a session handle without requiring it."""
    return getattr(value, name, None) if value is not None else None


def _cluster_issue(scenarios: list[Scenario]) -> str:
    """Use only oracle diagnoses when describing an agent's assigned issue."""
    diagnoses = list(dict.fromkeys(s.diagnosis for s in scenarios if s.diagnosis))
    return "; ".join(diagnoses)


# --------------------------------------------------------------------------- #
# Headless entrypoint
# --------------------------------------------------------------------------- #


async def run_headless(repo: str, sha: str, branch: str = "main") -> Run:
    """Drive one run without the API process. The demo fallback path."""
    from orchestrator.blackboard import Blackboard
    from orchestrator.bus import EventBus
    from orchestrator.devin.client import DevinClient
    from orchestrator.workspace import Workspace

    run = Run(repo=repo, branch=branch, commit_sha=sha)
    bus = EventBus()
    root = Path(os.getenv("ARTIFACTS_DIR", "artifacts")) / "workspaces" / run.id
    ctx = PipelineContext(
        run=run,
        workspace=Workspace(run_id=run.id, repo=repo, commit_sha=sha, root=root),
        bus=bus,
        blackboard=Blackboard(run.id, bus),
        devin=DevinClient(
            api_key=os.getenv("DEVIN_API_KEY", ""),
            api_base=os.getenv("DEVIN_API_BASE", "https://api.devin.ai/v1"),
            max_parallel=int(os.getenv("MAX_PARALLEL_AGENTS", "6")),
        ),
        default_suite_size=int(os.getenv("SUITE_SIZE", "50")),
    )
    return await Pipeline(ctx).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.pipeline")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--sha", required=True, help="pushed commit sha")
    parser.add_argument("--branch", default=os.getenv("TARGET_BRANCH", "main"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    run = asyncio.run(run_headless(args.repo, args.sha, args.branch))
    print(f"{run.id} finished in {run.stage.value}")
    if run.pull_request_url:
        print(run.pull_request_url)
    if run.error:
        print(f"error: {run.error}")
    return 0 if run.stage is not Stage.FAILED_UNRESOLVED else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
