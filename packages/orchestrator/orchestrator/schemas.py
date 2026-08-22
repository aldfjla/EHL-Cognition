"""Pydantic models mirroring ``packages/contracts/schemas/*.json``.

This module is the Python half of the contract. It is deliberately NOT a stub:
the pipeline, the roles, the API and the event bus all import from here, so the
shapes must exist before anything else can be built against them.

Mirroring rules (see ``packages/contracts/README.md``):
  * Field names match the JSON schema exactly — the wire format is what the
    dashboard parses, so no aliasing and no camelCase conversion.
  * Enum members match the JSON ``enum`` lists exactly, including case.
  * Changing a shape here without changing the ``.json`` is a bug.

Every model serialises with ``model_dump(mode="json")`` for transport.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """Timezone-aware UTC timestamp. All schema timestamps use this."""
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    """Short prefixed identifier, e.g. ``run_4f9c2a11``.

    Readable in logs and in the dashboard, unique enough for a single run.
    """
    return f"{prefix}_{uuid4().hex[:12]}"


class _Base(BaseModel):
    """Shared config: reject unknown fields so contract drift fails loudly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class Stage(str, Enum):
    """Pipeline stage. Mirrors ``run.json#/$defs/stage``.

    Ordering of the members is the happy-path order; ``pipeline.py`` owns the
    legal transitions, not this enum.
    """

    TRIGGERED = "TRIGGERED"
    RESOLVE_MODEL = "RESOLVE_MODEL"
    BUILD_HARNESS = "BUILD_HARNESS"
    DESIGN_SCENARIOS = "DESIGN_SCENARIOS"
    RUN_SUITE = "RUN_SUITE"
    CLUSTER_FAILURES = "CLUSTER_FAILURES"
    INVESTIGATE = "INVESTIGATE"
    FIX = "FIX"
    VERIFY = "VERIFY"
    REPORT = "REPORT"
    PR_OPENED = "PR_OPENED"
    PASSED_CLEAN = "PASSED_CLEAN"
    FAILED_UNRESOLVED = "FAILED_UNRESOLVED"

    @property
    def is_terminal(self) -> bool:
        """True for the three states a run can finish in."""
        return self in TERMINAL_STAGES

    @property
    def is_fanout(self) -> bool:
        """True for stages that dispatch many agents in parallel."""
        return self in (Stage.INVESTIGATE, Stage.FIX)


TERMINAL_STAGES: frozenset[Stage] = frozenset(
    {Stage.PASSED_CLEAN, Stage.PR_OPENED, Stage.FAILED_UNRESOLVED}
)


class Role(str, Enum):
    """A seat on the simulated engineering team. See ``docs/AGENT_ROLES.md``."""

    MODELER = "modeler"
    HARNESS_BUILDER = "harness_builder"
    SCENARIO_DESIGNER = "scenario_designer"
    INVESTIGATOR = "investigator"
    FIXER = "fixer"
    REVIEWER = "reviewer"
    REPORTER = "reporter"


class Speaker(str, Enum):
    """Anyone who can author a message: a role, or the orchestrator itself."""

    MODELER = "modeler"
    HARNESS_BUILDER = "harness_builder"
    SCENARIO_DESIGNER = "scenario_designer"
    INVESTIGATOR = "investigator"
    FIXER = "fixer"
    REVIEWER = "reviewer"
    REPORTER = "reporter"
    ORCHESTRATOR = "orchestrator"
    BROADCAST = "broadcast"


class AgentStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    WORKING = "working"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            AgentStatus.SUCCEEDED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        )


class MessageKind(str, Enum):
    """Speech act of a relayed message. Drives icon and colour in TeamChat."""

    HYPOTHESIS = "hypothesis"
    FINDING = "finding"
    QUESTION = "question"
    ANSWER = "answer"
    VERDICT = "verdict"
    HANDOFF = "handoff"


class ScenarioStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"  # the sim broke, distinct from the robot failing the task


class FindingKind(str, Enum):
    ROOT_CAUSE = "root_cause"
    PATCH = "patch"
    CONSTRAINT = "constraint"
    OBSERVATION = "observation"
    VERIFICATION = "verification"


class FindingStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    SUPERSEDED = "superseded"


class ModelSource(str, Enum):
    """How the robot's physical model was obtained. Library-first."""

    MENAGERIE = "menagerie"
    REPO = "repo"
    GENERATED = "generated"


class Verdict(str, Enum):
    CLEAN = "clean"
    FIXED = "fixed"
    UNRESOLVED = "unresolved"


class EventType(str, Enum):
    """Mirrors ``event.json#/properties/type``."""

    RUN_CREATED = "run.created"
    RUN_STAGE_CHANGED = "run.stage_changed"
    RUN_FINISHED = "run.finished"
    AGENT_CREATED = "agent.created"
    AGENT_UPDATED = "agent.updated"
    AGENT_STATUS_CHANGED = "agent.status_changed"
    AGENT_ACTIVITY = "agent.activity"
    MESSAGE_SENT = "message.sent"
    SCENARIO_CREATED = "scenario.created"
    SCENARIO_STARTED = "scenario.started"
    SCENARIO_PROGRESS = "scenario.progress"
    SCENARIO_FINISHED = "scenario.finished"
    SUITE_PROGRESS = "suite.progress"
    WORKER_POOL_CHANGED = "worker.pool_changed"
    FINDING_CREATED = "finding.created"
    FINDING_UPDATED = "finding.updated"
    ARTIFACT_CREATED = "artifact.created"
    REPORT_CREATED = "report.created"
    ERROR = "error"


# --------------------------------------------------------------------------- #
# Leaf objects
# --------------------------------------------------------------------------- #


class RobotModel(_Base):
    """The resolved physical model the suite simulates against."""

    source: ModelSource
    name: str | None = None
    model_path: str = Field(description="Absolute path to the MJCF file.")
    dof: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Modeler's confidence that this matches the real hardware.",
    )


class SuiteStats(_Base):
    """Aggregate pass/fail counts for one execution of the scenario matrix."""

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    baseline_pass_rate: float | None = None

    @classmethod
    def from_counts(cls, passed: int, failed: int, **kw: Any) -> SuiteStats:
        total = passed + failed
        return cls(
            total=total,
            passed=passed,
            failed=failed,
            pass_rate=(passed / total) if total else 0.0,
            **kw,
        )


class CriterionResult(_Base):
    """Outcome of one success criterion from ``robotci.yaml`` ``task.success``."""

    id: str
    passed: bool
    value: float | str | None = None
    threshold: float | str | None = None


class Ref(_Base):
    """A pointer from a message to the evidence behind it."""

    type: Literal["scenario", "finding", "artifact", "commit", "cluster", "agent"]
    id: str
    label: str | None = None


# --------------------------------------------------------------------------- #
# Top-level objects
# --------------------------------------------------------------------------- #


class Run(_Base):
    """One CI run: a single push, taken from trigger to a terminal stage."""

    id: str = Field(default_factory=lambda: _new_id("run"))
    stage: Stage = Stage.TRIGGERED
    repo: str
    branch: str = "main"
    commit_sha: str
    commit_message: str = ""
    pushed_by: str = ""
    robot_model: RobotModel | None = None
    suite: SuiteStats | None = None
    pull_request_url: str | None = None
    report_id: str | None = None
    error: str | None = Field(
        default=None,
        description="Infrastructure failure, not a robot failure.",
    )
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class RepoRunSummary(_Base):
    """The latest run shown alongside a connected repository."""

    id: str
    stage: Stage
    created_at: datetime


class Repo(_Base):
    """A GitHub repository connected to Robot CI."""

    id: str = Field(default_factory=lambda: _new_id("repo"))
    full_name: str
    branch: str = "main"
    suite_size: int = Field(default=50, ge=1)
    #: Extra watched branch patterns beyond ``branch``. Empty means "only
    #: ``branch``". Populated from the repo's ``robotci.yaml`` ``ci.branches``
    #: after the first checkout — see :mod:`orchestrator.triggers`.
    branches: list[str] = Field(default_factory=list)
    #: Path globs a push must touch to start a run, and globs subtracted from
    #: them. ``None`` means unset (use the built-in defaults); an *empty list*
    #: is a configured value meaning "nothing" — ``paths.exclude: []`` in a
    #: repo's ``robotci.yaml`` disables the default exclusions and must survive
    #: the round-trip through storage.
    path_include: list[str] | None = None
    path_exclude: list[str] | None = None
    #: Where the filters above came from: ``"default"`` until a checkout has
    #: been read, then ``"robotci.yaml"`` or ``"registry"`` when set by API.
    filters_source: Literal["default", "registry", "robotci.yaml"] = "default"
    created_at: datetime = Field(default_factory=_now)
    last_push_at: datetime | None = None
    status: Literal["dormant", "running"] = "dormant"
    latest_run: RepoRunSummary | None = None


class Agent(_Base):
    """One Devin session wrapped in a role. A card in the dashboard grid."""

    id: str = Field(default_factory=lambda: _new_id("agt"))
    run_id: str
    session_id: str | None = None
    session_url: str | None = None
    role: Role
    title: str = ""
    task: str = ""
    status: AgentStatus = AgentStatus.QUEUED
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=3, ge=1)
    cluster_id: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    parent_agent_id: str | None = None
    finding_ids: list[str] = Field(default_factory=list)
    last_activity: str | None = Field(
        default=None,
        description="Latest transcript line, for the live activity ticker.",
    )
    desktop_url: str | None = Field(
        default=None,
        description="Embeddable live view of the agent's machine, when the "
        "session exposes one. Null means the dashboard shows the ticker "
        "instead of a dead frame.",
    )
    issue: str | None = Field(
        default=None,
        description="The failure being worked on, in the oracle's words. "
        "Distinct from ``task``, which is our instruction.",
    )
    step: str | None = Field(
        default=None,
        description="Coarse phase inside the agent's own work.",
    )
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class Message(_Base):
    """One orchestrator-mediated relay between two agents.

    Devin sessions cannot address each other. Every element of this object is
    produced by the orchestrator when it copies a finding from one session's
    output into another session's prompt. See ``docs/AGENT_ROLES.md``.
    """

    id: str = Field(default_factory=lambda: _new_id("msg"))
    run_id: str
    from_agent_id: str | None = None
    to_agent_id: str | None = None
    from_role: Speaker
    to_role: Speaker
    kind: MessageKind
    body: str
    refs: list[Ref] = Field(default_factory=list)
    ts: datetime = Field(default_factory=_now)


class Scenario(_Base):
    """One randomized world plus the result of running the pushed code in it."""

    id: str = Field(default_factory=lambda: _new_id("scn"))
    run_id: str
    index: int = Field(ge=0)
    seed: int = Field(
        description="Replaying this seed reproduces the world exactly. "
        "This is what makes a failure reproducible for the Investigator."
    )
    label: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    status: ScenarioStatus = ScenarioStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    duration_s: float | None = None
    sim_time_s: float | None = None
    criteria: list[CriterionResult] = Field(default_factory=list)
    diagnosis: str | None = Field(
        default=None,
        description="The oracle's human-readable explanation of the failure. "
        "Primary input to the Investigator — measured, not guessed.",
    )
    cluster_id: str | None = None
    video_path: str | None = None
    live_frame_path: str | None = Field(
        default=None,
        description="Most recent rendered frame while running, overwritten in "
        "place. Null once ``video_path`` takes over.",
    )
    worker_id: str | None = None
    progress: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of the simulated horizon completed. Advisory "
        "only — never a success signal.",
    )
    trace_path: str | None = None
    error: str | None = None


class Finding(_Base):
    """A unit of knowledge on the shared blackboard."""

    id: str = Field(default_factory=lambda: _new_id("fnd"))
    run_id: str
    author_agent_id: str | None = None
    author_role: Speaker
    kind: FindingKind
    summary: str = Field(description="One sentence, quoted into other prompts.")
    detail: str = ""
    cluster_id: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: FindingStatus = FindingStatus.PROPOSED
    superseded_by: str | None = None
    created_at: datetime = Field(default_factory=_now)


class Incident(_Base):
    """One investigated failure cluster, as written up in the report."""

    cluster_id: str
    title: str
    affected_scenarios: int = Field(default=0, ge=0)
    root_cause: str
    resolution: str
    files_changed: list[str] = Field(default_factory=list)
    before_video: str | None = None
    after_video: str | None = None
    status: Literal["fixed", "unresolved"] = "unresolved"


class Report(_Base):
    """The written incident report. Doubles as the pull request body."""

    id: str = Field(default_factory=lambda: _new_id("rpt"))
    run_id: str
    verdict: Verdict
    title: str
    summary: str
    incidents: list[Incident] = Field(default_factory=list)
    diff: str | None = None
    before: SuiteStats | None = None
    after: SuiteStats | None = None
    pull_request_url: str | None = None
    markdown_path: str | None = None
    created_at: datetime = Field(default_factory=_now)


class Event(_Base):
    """The envelope pushed over ``WS /ws/runs/{id}``.

    ``data`` is intentionally loose: it carries either a full object from this
    module (already ``model_dump``ed) or a partial patch for ``*_changed``
    events. ``docs/EVENT_PROTOCOL.md`` specifies the payload per type.
    """

    id: str = Field(default_factory=lambda: _new_id("evt"))
    run_id: str
    seq: int = Field(default=0, ge=0)
    type: EventType
    ts: datetime = Field(default_factory=_now)
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Failure clustering
# --------------------------------------------------------------------------- #


class Cluster(_Base):
    """A group of scenarios believed to share one root cause.

    Produced by ``clustering.py`` at CLUSTER_FAILURES. One cluster becomes one
    Investigator agent, which is how fan-out width is decided.
    """

    id: str = Field(default_factory=lambda: _new_id("cls"))
    run_id: str
    label: str = Field(description="Short name, e.g. 'gripper closes early'.")
    scenario_ids: list[str] = Field(default_factory=list)
    signature: str = Field(
        default="",
        description="Normalised diagnosis text the grouping keyed on.",
    )
    size: int = Field(default=0, ge=0)


__all__ = [
    "TERMINAL_STAGES",
    "Agent",
    "AgentStatus",
    "Cluster",
    "CriterionResult",
    "Event",
    "EventType",
    "Finding",
    "FindingKind",
    "FindingStatus",
    "Incident",
    "Message",
    "MessageKind",
    "ModelSource",
    "Ref",
    "Repo",
    "RepoRunSummary",
    "Report",
    "RobotModel",
    "Role",
    "Run",
    "Scenario",
    "ScenarioStatus",
    "Speaker",
    "Stage",
    "SuiteStats",
    "Verdict",
]

# The models above are hand-mirrored from packages/contracts/schemas/*.json,
# which is the source of truth the dashboard parses. tests/test_contracts.py
# walks those files and asserts every property name and enum member lines up —
# hand-mirroring only stays honest if something checks it.
