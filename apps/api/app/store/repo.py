"""Query helpers and the row <-> pydantic conversion layer.

Responsibility
--------------
The only module that knows both :mod:`orchestrator.schemas` and
:mod:`app.store.tables`. Routers and the pipeline speak pydantic; the database
speaks rows; every translation happens here so a column rename breaks one file.

Inputs:  a session plus ids/filters.
Outputs: pydantic models from :mod:`orchestrator.schemas`, never raw rows.

Rule: no function here returns a ``*Row``. Leaking rows upward is how ORM
details end up in a React component's prop types.
"""

from __future__ import annotations

from typing import Any

from orchestrator.schemas import (
    Agent,
    Cluster,
    Finding,
    Message,
    Report,
    Run,
    Scenario,
)

# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


def row_to_run(row: Any) -> Run:
    """Inflate a RunRow, parsing its JSON columns."""
    raise NotImplementedError
    # TODO(build): json.loads robot_model_json/suite_json, construct Run.


def run_to_row(run: Run) -> Any:
    """Flatten a Run for storage, serialising nested objects to JSON."""
    raise NotImplementedError
    # TODO(build): model_dump(mode="json") the nested pieces, build RunRow.


# TODO(build): the same pair for Agent, Scenario, Message, Finding, Report,
# Cluster. Consider one generic pair driven by tables._json_columns() rather
# than twelve near-identical functions.


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


def create_run(db: Any, run: Run) -> Run:
    """Insert a new run."""
    raise NotImplementedError
    # TODO(build): add + flush + return inflated.


def get_run(db: Any, run_id: str) -> Run | None:
    """Fetch one run, or None."""
    raise NotImplementedError
    # TODO(build): db.get(RunRow, run_id).


def list_runs(db: Any, limit: int = 25, offset: int = 0) -> list[Run]:
    """Runs newest first."""
    raise NotImplementedError
    # TODO(build): select ordered by created_at desc.


def update_run(db: Any, run: Run) -> Run:
    """Persist mutations, stamping ``updated_at``."""
    raise NotImplementedError
    # TODO(build): merge the row, set updated_at=now.


# --------------------------------------------------------------------------- #
# Agents, scenarios, messages, findings, clusters, reports
# --------------------------------------------------------------------------- #


def upsert_agent(db: Any, agent: Agent) -> Agent:
    """Insert or update an agent — status changes many times per run."""
    raise NotImplementedError
    # TODO(build): merge by primary key.


def list_agents(db: Any, run_id: str) -> list[Agent]:
    """A run's agents in creation order."""
    raise NotImplementedError
    # TODO(build): select ordered by created_at asc.


def upsert_scenario(db: Any, scenario: Scenario) -> Scenario:
    """Insert or update one scenario result."""
    raise NotImplementedError
    # TODO(build): merge on (run_id, seed, attempt), not on id alone.


def list_scenarios(db: Any, run_id: str, attempt: int | None = None) -> list[Scenario]:
    """A run's scenarios, ordered by index. Filter by attempt for before/after."""
    raise NotImplementedError
    # TODO(build): select with optional attempt filter, order by index_.


def add_message(db: Any, message: Message) -> Message:
    """Append to the relay log."""
    raise NotImplementedError
    # TODO(build): insert.


def list_messages(
    db: Any,
    run_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 200,
) -> list[Message]:
    """Relay traffic for a run or one agent, oldest first."""
    raise NotImplementedError
    # TODO(build): filter on run_id, or on (from_agent_id | to_agent_id).


def upsert_finding(db: Any, finding: Finding) -> Finding:
    """Insert or update a blackboard finding."""
    raise NotImplementedError
    # TODO(build): merge by id.


def list_findings(db: Any, run_id: str, status: str | None = None) -> list[Finding]:
    """A run's findings, optionally filtered by status."""
    raise NotImplementedError
    # TODO(build): select with optional status filter.


def upsert_cluster(db: Any, cluster: Cluster) -> Cluster:
    """Insert or update a failure cluster."""
    raise NotImplementedError
    # TODO(build): merge by id.


def list_clusters(db: Any, run_id: str) -> list[Cluster]:
    """A run's clusters, largest first."""
    raise NotImplementedError
    # TODO(build): select ordered by size desc.


def save_report(db: Any, report: Report) -> Report:
    """Persist the incident report and link it to its run."""
    raise NotImplementedError
    # TODO(build): insert ReportRow, set run.report_id.


def get_report(db: Any, run_id: str) -> Report | None:
    """The report for a run, if REPORT has completed."""
    raise NotImplementedError
    # TODO(build): select by run_id.
