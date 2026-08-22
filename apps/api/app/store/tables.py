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
from typing import Any

from sqlmodel import Field, SQLModel


class RunRow(SQLModel, table=True):
    """Mirrors ``run.json``. See :class:`orchestrator.schemas.Run`."""

    __tablename__ = "runs"

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

    # TODO(build): add __table_args__ index on (repo, created_at desc) for the
    # index page query.


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
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class ScenarioRow(SQLModel, table=True):
    """Mirrors ``scenario.json``."""

    __tablename__ = "scenarios"

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
    trace_path: str | None = None
    error: str | None = None

    # TODO(build): (run_id, seed, attempt) should be unique — VERIFY re-runs
    # the same seeds and must not silently overwrite the baseline row, because
    # the before/after comparison depends on both existing.


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


def _json_columns() -> dict[str, Any]:
    """Documentation hook: which columns hold JSON blobs, for repo.py.

    Kept as a function rather than a comment so the conversion layer can assert
    against it instead of drifting.
    """
    raise NotImplementedError
    # TODO(build): return {table: [column names]} and use it in repo.py's
    # to_model/from_model helpers.
