"""SQLModel tables — the durable half of the contracts.

Responsibility
--------------
Persist runs, agents, scenarios, messages, findings and reports.

Relationship to the contracts
-----------------------------
These tables mirror ``packages/contracts/schemas/*.json``, but not one-to-one:
the pydantic models in :mod:`orchestrator.schemas` are the wire format, and
these are storage. Two deliberate differences:

* Nested objects (``robot_model``, ``suite``, ``criteria``, ``params``,
  ``refs``, ``incidents``) are stored as JSON columns rather than normalised
  into child tables. They are read as a unit and never queried into, so
  normalising them would buy nothing and cost every read a join.
* Enums are stored as their string values so a schema addition does not
  invalidate existing rows.

Conversion between the two lives in :mod:`app.store.repo`, in one place.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class RunRow(SQLModel, table=True):
    """Mirrors ``run.json``. See :class:`orchestrator.schemas.Run`."""

    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_repo_created_at", "repo", "created_at"),)

    id: str = Field(primary_key=True)
    stage: str = Field(index=True)
    repo: str = Field(index=True)
    branch: str = "main"
    commit_sha: str = Field(index=True)
    commit_message: str = ""
    pushed_by: str = ""
    robot_model_json: str | None = None
    suite_json: str | None = None
    pull_request_url: str | None = None
    report_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class RepoRow(SQLModel, table=True):
    """A GitHub repository connected to Robot CI."""

    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("full_name", name="uq_repos_full_name"),)

    id: str = Field(primary_key=True)
    full_name: str = Field(index=True)
    branch: str = "main"
    suite_size: int = 50
    # Trigger filters. Stored as JSON lists for the same reason as the run's
    # nested objects: they are read as a unit and never queried into.
    branches_json: str = "[]"
    path_include_json: str = "[]"
    path_exclude_json: str = "[]"
    filters_source: str = "default"
    created_at: datetime
    last_push_at: datetime | None = None


class AgentRow(SQLModel, table=True):
    """Mirrors ``agent.json``."""

    __tablename__ = "agents"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    session_id: str | None = Field(default=None, index=True)
    session_url: str | None = None
    role: str
    title: str = ""
    task: str = ""
    status: str = Field(index=True)
    iteration: int = 0
    max_iterations: int = 3
    cluster_id: str | None = Field(default=None, index=True)
    scenario_ids_json: str = "[]"
    parent_agent_id: str | None = None
    finding_ids_json: str = "[]"
    last_activity: str | None = None
    desktop_url: str | None = None
    issue: str | None = None
    step: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class ScenarioRow(SQLModel, table=True):
    """Mirrors ``scenario.json``."""

    __tablename__ = "scenarios"
    # VERIFY re-runs the same seeds and must not silently overwrite the
    # baseline row: the before/after comparison depends on both existing.
    __table_args__ = (
        UniqueConstraint(
            "run_id", "seed", "attempt", name="uq_scenarios_run_seed_attempt"
        ),
    )

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    index_: int = Field(alias="index")
    seed: int = Field(index=True)
    label: str = ""
    params_json: str = "{}"
    status: str = Field(index=True)
    attempt: int = 1
    duration_s: float | None = None
    sim_time_s: float | None = None
    criteria_json: str = "[]"
    diagnosis: str | None = None
    cluster_id: str | None = Field(default=None, index=True)
    video_path: str | None = None
    live_frame_path: str | None = None
    worker_id: str | None = None
    progress: float | None = None
    trace_path: str | None = None
    error: str | None = None


class MessageRow(SQLModel, table=True):
    """Mirrors ``message.json`` — the relay log."""

    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    from_agent_id: str | None = Field(default=None, index=True)
    to_agent_id: str | None = Field(default=None, index=True)
    from_role: str
    to_role: str
    kind: str
    body: str
    refs_json: str = "[]"
    ts: datetime = Field(index=True)


class FindingRow(SQLModel, table=True):
    """Mirrors ``finding.json`` — the persisted blackboard."""

    __tablename__ = "findings"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    author_agent_id: str | None = None
    author_role: str
    kind: str = Field(index=True)
    summary: str
    detail: str = ""
    cluster_id: str | None = Field(default=None, index=True)
    scenario_ids_json: str = "[]"
    files_json: str = "[]"
    confidence: float = 0.5
    status: str = Field(index=True)
    superseded_by: str | None = None
    created_at: datetime


class ReportRow(SQLModel, table=True):
    """Mirrors ``report.json``."""

    __tablename__ = "reports"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    verdict: str
    title: str
    summary: str
    incidents_json: str = "[]"
    diff: str | None = None
    before_json: str | None = None
    after_json: str | None = None
    pull_request_url: str | None = None
    markdown_path: str | None = None
    created_at: datetime


class ClusterRow(SQLModel, table=True):
    """Failure clusters. No JSON schema of its own — internal to a run."""

    __tablename__ = "clusters"

    id: str = Field(primary_key=True)
    run_id: str = Field(index=True, foreign_key="runs.id")
    label: str = ""
    signature: str = ""
    size: int = 0
    scenario_ids_json: str = "[]"


def _json_columns() -> dict[str, list[str]]:
    """Documentation hook: which columns hold JSON blobs, for repo.py.

    Kept as a function rather than a comment so the conversion layer can assert
    against it instead of drifting.
    """
    return {
        "runs": ["robot_model_json", "suite_json"],
        # ``repos`` is converted by hand in repo.py: Repo carries derived
        # fields (status, latest_run) that have no column, so the generic
        # mapping cannot own it. Listed here so the inventory stays complete.
        "repos": ["branches_json", "path_include_json", "path_exclude_json"],
        "agents": ["scenario_ids_json", "finding_ids_json"],
        "scenarios": ["params_json", "criteria_json"],
        "messages": ["refs_json"],
        "findings": ["scenario_ids_json", "files_json"],
        "reports": ["incidents_json", "before_json", "after_json"],
        "clusters": ["scenario_ids_json"],
    }
