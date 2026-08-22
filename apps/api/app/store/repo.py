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

Conversion strategy
-------------------
One generic pair (:func:`_to_row` / :func:`_to_model`) driven by a per-model
:class:`_Mapping`, rather than fourteen near-identical functions. The mapping
declares only what differs from the wire format: which fields are stored as
JSON blobs, and the one field whose column name differs (``index`` is a
reserved-ish word, so the column is ``index_``). ``_assert_mappings_match_tables``
checks the declarations against :func:`app.store.tables._json_columns` at import
time, so a column added to a table without a mapping fails loudly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from orchestrator.schemas import (
    Agent,
    Cluster,
    Finding,
    Message,
    Repo,
    Report,
    Run,
    Scenario,
)
from pydantic import BaseModel
from sqlmodel import Session, SQLModel, select

from app.store.tables import (
    AgentRow,
    ClusterRow,
    FindingRow,
    MessageRow,
    RepoRow,
    ReportRow,
    RunRow,
    ScenarioRow,
    _json_columns,
)

# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Mapping:
    """How one pydantic model maps onto its table.

    ``json_fields`` are model fields persisted as a ``<name>_json`` text column;
    ``renames`` maps a model field name to a differently named column.
    """

    model: type[BaseModel]
    row: type[SQLModel]
    json_fields: tuple[str, ...] = ()
    renames: dict[str, str] = field(default_factory=dict)


_MAPPINGS: dict[type[BaseModel], _Mapping] = {
    Run: _Mapping(Run, RunRow, ("robot_model", "suite")),
    Agent: _Mapping(Agent, AgentRow, ("scenario_ids", "finding_ids")),
    Scenario: _Mapping(
        Scenario, ScenarioRow, ("params", "criteria"), renames={"index": "index_"}
    ),
    Message: _Mapping(Message, MessageRow, ("refs",)),
    Finding: _Mapping(Finding, FindingRow, ("scenario_ids", "files")),
    Report: _Mapping(Report, ReportRow, ("incidents", "before", "after")),
    Cluster: _Mapping(Cluster, ClusterRow, ("scenario_ids",)),
}


def _assert_mappings_match_tables() -> None:
    """Fail at import if a JSON column exists with no mapping behind it."""
    declared = _json_columns()
    for mapping in _MAPPINGS.values():
        table = str(mapping.row.__tablename__)
        expected = sorted(f"{name}_json" for name in mapping.json_fields)
        if sorted(declared.get(table, [])) != expected:
            raise RuntimeError(
                f"JSON column drift for {table}: tables declares "
                f"{sorted(declared.get(table, []))}, repo maps {expected}"
            )


_assert_mappings_match_tables()


def _to_row(model: BaseModel) -> Any:
    """Flatten a pydantic model into its row, serialising nested objects."""
    mapping = _MAPPINGS[type(model)]
    data = model.model_dump(mode="json")

    for name in mapping.json_fields:
        value = data.pop(name)
        data[f"{name}_json"] = None if value is None else json.dumps(value)

    # ``mode="json"`` stringifies datetimes; the columns want real datetimes.
    for name in list(data):
        attr = getattr(model, name, None)
        if isinstance(attr, datetime):
            data[name] = attr

    for model_name, column in mapping.renames.items():
        data[column] = data.pop(model_name)

    return mapping.row(**data)


def _to_model[M: BaseModel](row: Any, model_type: type[M]) -> M:
    """Inflate a row into its pydantic model, parsing JSON columns."""
    mapping = _MAPPINGS[model_type]
    data: dict[str, Any] = {
        name: getattr(row, name) for name in mapping.row.model_fields
    }

    for name in mapping.json_fields:
        raw = data.pop(f"{name}_json")
        if raw:
            data[name] = json.loads(raw)

    for model_name, column in mapping.renames.items():
        data[model_name] = data.pop(column)

    # SQLite hands back naive datetimes; the wire format is UTC-aware.
    for name, value in data.items():
        if isinstance(value, datetime) and value.tzinfo is None:
            data[name] = value.replace(tzinfo=UTC)

    return model_type.model_validate({k: v for k, v in data.items() if v is not None})


def row_to_run(row: Any) -> Run:
    """Inflate a RunRow, parsing its JSON columns."""
    return _to_model(row, Run)


def run_to_row(run: Run) -> RunRow:
    """Flatten a Run for storage, serialising nested objects to JSON."""
    return _to_row(run)


def _merge(db: Session, model: BaseModel) -> Any:
    """Insert-or-update ``model`` by primary key and return the persisted row."""
    row = db.merge(_to_row(model))
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


def create_run(db: Session, run: Run) -> Run:
    """Insert a new run."""
    row = run_to_row(run)
    db.add(row)
    db.flush()
    return row_to_run(row)


def get_run(db: Session, run_id: str) -> Run | None:
    """Fetch one run, or None."""
    row = db.get(RunRow, run_id)
    return row_to_run(row) if row else None


def list_runs(db: Session, limit: int = 25, offset: int = 0) -> list[Run]:
    """Runs newest first."""
    rows = db.exec(
        select(RunRow).order_by(RunRow.created_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return [row_to_run(row) for row in rows]


def find_active_run(db: Session, repo: str, commit_sha: str) -> Run | None:
    """A non-terminal run already in flight for this ``(repo, sha)``.

    GitHub redelivers webhooks, and two pipelines racing on one repo would fight
    over branches — the webhook router uses this to dedupe.
    """
    rows = db.exec(
        select(RunRow)
        .where(RunRow.repo == repo, RunRow.commit_sha == commit_sha)
        .order_by(RunRow.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    for row in rows:
        run = row_to_run(row)
        if not run.stage.is_terminal:
            return run
    return None


def update_run(db: Session, run: Run) -> Run:
    """Persist mutations, stamping ``updated_at``."""
    run = run.model_copy(update={"updated_at": datetime.now(UTC)})
    return row_to_run(_merge(db, run))


# --------------------------------------------------------------------------- #
# Connected repositories
# --------------------------------------------------------------------------- #


def _repo_to_row(repository: Repo) -> RepoRow:
    """Flatten a connected repository for storage."""
    return RepoRow(
        id=repository.id,
        full_name=repository.full_name,
        branch=repository.branch,
        suite_size=repository.suite_size,
        created_at=repository.created_at,
        last_push_at=repository.last_push_at,
    )


def _row_to_repo(row: RepoRow) -> Repo:
    """Inflate a connected repository from storage."""
    values: dict[str, Any] = {
        "id": row.id,
        "full_name": row.full_name,
        "branch": row.branch,
        "suite_size": row.suite_size,
        "created_at": row.created_at,
        "last_push_at": row.last_push_at,
    }
    for name, value in values.items():
        if isinstance(value, datetime) and value.tzinfo is None:
            values[name] = value.replace(tzinfo=UTC)
    return Repo.model_validate(values)


def create_repo(db: Session, repository: Repo) -> Repo:
    """Insert a connected repository."""
    row = _repo_to_row(repository)
    db.add(row)
    db.flush()
    return _row_to_repo(row)


def list_repos(db: Session) -> list[Repo]:
    """Connected repositories, newest first."""
    rows = db.exec(
        select(RepoRow).order_by(RepoRow.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [_row_to_repo(row) for row in rows]


def get_repo(db: Session, repo_id: str) -> Repo | None:
    """Fetch a connected repository by id."""
    row = db.get(RepoRow, repo_id)
    return _row_to_repo(row) if row else None


def get_repo_by_full_name(db: Session, full_name: str) -> Repo | None:
    """Fetch a connected repository by its GitHub ``owner/name``."""
    row = db.exec(select(RepoRow).where(RepoRow.full_name == full_name)).first()
    return _row_to_repo(row) if row else None


def get_by_full_name(db: Session, full_name: str) -> Repo | None:
    """Compatibility alias for looking up a connected repository by name."""
    return get_repo_by_full_name(db, full_name)


def update_repo(db: Session, repository: Repo) -> Repo:
    """Persist changes to a connected repository."""
    row = db.get(RepoRow, repository.id)
    if row is None:
        raise KeyError(repository.id)
    row.full_name = repository.full_name
    row.branch = repository.branch
    row.suite_size = repository.suite_size
    row.created_at = repository.created_at
    row.last_push_at = repository.last_push_at
    db.add(row)
    db.flush()
    return _row_to_repo(row)


def delete_repo(db: Session, repo_id: str) -> bool:
    """Delete a connected repository, returning whether it existed."""
    row = db.get(RepoRow, repo_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def list_runs_for_repo(db: Session, repo_name: str) -> list[Run]:
    """Runs for one repository, newest first."""
    rows = db.exec(
        select(RunRow)
        .where(RunRow.repo == repo_name)
        .order_by(RunRow.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [row_to_run(row) for row in rows]


# --------------------------------------------------------------------------- #
# Agents, scenarios, messages, findings, clusters, reports
# --------------------------------------------------------------------------- #


def upsert_agent(db: Session, agent: Agent) -> Agent:
    """Insert or update an agent — status changes many times per run."""
    return _to_model(_merge(db, agent), Agent)


def get_agent(db: Session, agent_id: str) -> Agent | None:
    """One agent, or None."""
    row = db.get(AgentRow, agent_id)
    return _to_model(row, Agent) if row else None


def list_agents(db: Session, run_id: str) -> list[Agent]:
    """A run's agents in creation order."""
    rows = db.exec(
        select(AgentRow).where(AgentRow.run_id == run_id).order_by(AgentRow.created_at)  # type: ignore[arg-type]
    ).all()
    return [_to_model(row, Agent) for row in rows]


def upsert_scenario(db: Session, scenario: Scenario) -> Scenario:
    """Insert or update one scenario result.

    Identity is ``(run_id, seed, attempt)``, not the id: VERIFY re-runs the same
    seeds as a new attempt and both rows must survive for the before/after
    comparison to exist.
    """
    existing = db.exec(
        select(ScenarioRow).where(
            ScenarioRow.run_id == scenario.run_id,
            ScenarioRow.seed == scenario.seed,
            ScenarioRow.attempt == scenario.attempt,
        )
    ).first()
    if existing is not None:
        scenario = scenario.model_copy(update={"id": existing.id})
    return _to_model(_merge(db, scenario), Scenario)


def get_scenario(db: Session, scenario_id: str) -> Scenario | None:
    """Fetch one scenario by id, or ``None`` when it is absent."""
    row = db.get(ScenarioRow, scenario_id)
    return _to_model(row, Scenario) if row else None


def list_scenarios(
    db: Session, run_id: str, attempt: int | None = None
) -> list[Scenario]:
    """A run's scenarios, ordered by index. Filter by attempt for before/after."""
    statement = select(ScenarioRow).where(ScenarioRow.run_id == run_id)
    if attempt is not None:
        statement = statement.where(ScenarioRow.attempt == attempt)
    rows = db.exec(statement.order_by(ScenarioRow.attempt, ScenarioRow.index_)).all()  # type: ignore[arg-type]
    return [_to_model(row, Scenario) for row in rows]


def add_message(db: Session, message: Message) -> Message:
    """Append to the relay log."""
    row = _to_row(message)
    db.add(row)
    db.flush()
    return _to_model(row, Message)


def list_messages(
    db: Session,
    run_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 200,
) -> list[Message]:
    """Relay traffic for a run or one agent, oldest first."""
    statement = select(MessageRow)
    if run_id is not None:
        statement = statement.where(MessageRow.run_id == run_id)
    if agent_id is not None:
        statement = statement.where(
            (MessageRow.from_agent_id == agent_id)
            | (MessageRow.to_agent_id == agent_id)
        )
    rows = db.exec(statement.order_by(MessageRow.ts).limit(limit)).all()  # type: ignore[arg-type]
    return [_to_model(row, Message) for row in rows]


def upsert_finding(db: Session, finding: Finding) -> Finding:
    """Insert or update a blackboard finding."""
    return _to_model(_merge(db, finding), Finding)


def list_findings(db: Session, run_id: str, status: str | None = None) -> list[Finding]:
    """A run's findings, optionally filtered by status."""
    statement = select(FindingRow).where(FindingRow.run_id == run_id)
    if status is not None:
        statement = statement.where(FindingRow.status == status)
    rows = db.exec(statement.order_by(FindingRow.created_at)).all()  # type: ignore[arg-type]
    return [_to_model(row, Finding) for row in rows]


def upsert_cluster(db: Session, cluster: Cluster) -> Cluster:
    """Insert or update a failure cluster."""
    return _to_model(_merge(db, cluster), Cluster)


def list_clusters(db: Session, run_id: str) -> list[Cluster]:
    """A run's clusters, largest first."""
    rows = db.exec(
        select(ClusterRow)
        .where(ClusterRow.run_id == run_id)
        .order_by(ClusterRow.size.desc())  # type: ignore[attr-defined]
    ).all()
    return [_to_model(row, Cluster) for row in rows]


def save_report(db: Session, report: Report) -> Report:
    """Persist the incident report and link it to its run."""
    row = _merge(db, report)
    run_row = db.get(RunRow, report.run_id)
    if run_row is not None:
        run_row.report_id = report.id
        run_row.pull_request_url = report.pull_request_url or run_row.pull_request_url
        run_row.updated_at = datetime.now(UTC)
        db.add(run_row)
        db.flush()
    return _to_model(row, Report)


def get_report(db: Session, run_id: str) -> Report | None:
    """The report for a run, if REPORT has completed."""
    row = db.exec(
        select(ReportRow)
        .where(ReportRow.run_id == run_id)
        .order_by(ReportRow.created_at.desc())  # type: ignore[attr-defined]
    ).first()
    return _to_model(row, Report) if row else None
