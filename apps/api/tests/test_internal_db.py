"""Metadata-driven access to the internal database browser."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from orchestrator.schemas import Agent, Role

from app.store import repo


def test_table_listing_exposes_runs_and_columns(client: TestClient, db: Any) -> None:
    tables = client.get("/internal/db/tables")

    assert tables.status_code == 200
    runs = next(table for table in tables.json() if table["name"] == "runs")
    assert runs["primary_key"] == "id"
    assert runs["row_count"] == 0
    assert {column["name"] for column in runs["columns"]} >= {
        "id",
        "commit_message",
        "created_at",
    }


def test_rows_paginate_and_serialize_datetimes(client: TestClient, run: Any) -> None:
    response = client.get(
        "/internal/db/tables/runs/rows",
        params={"limit": 1, "offset": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id"] == run.id
    assert body["rows"][0]["created_at"] == run.created_at.isoformat().replace(
        "+00:00", "Z"
    )
    next_page = client.get(
        "/internal/db/tables/runs/rows",
        params={"limit": 1, "offset": 1},
    )
    assert next_page.status_code == 200
    assert next_page.json()["rows"] == []


def test_patch_updates_real_column_and_rejects_invalid_edits(
    client: TestClient, run: Any
) -> None:
    updated = client.patch(
        f"/internal/db/tables/runs/rows/{run.id}",
        json={"values": {"commit_message": "updated in browser"}},
    )
    assert updated.status_code == 200
    assert updated.json()["commit_message"] == "updated in browser"

    assert (
        client.patch(
            f"/internal/db/tables/runs/rows/{run.id}",
            json={"values": {"not_a_column": "nope"}},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/internal/db/tables/runs/rows/{run.id}",
            json={"values": {"id": "changed"}},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            "/internal/db/tables/runs/rows/missing",
            json={"values": {"commit_message": "no row"}},
        ).status_code
        == 404
    )


def test_delete_removes_leaf_and_rejects_rows_with_children(
    client: TestClient, db: Any, run: Any
) -> None:
    leaf = repo.create_run(
        db,
        run.model_copy(update={"id": "run-leaf", "commit_message": "leaf"}),
    )
    db.commit()
    assert client.delete(f"/internal/db/tables/runs/rows/{leaf.id}").status_code == 204
    assert client.get(f"/runs/{leaf.id}").status_code == 404

    repo.upsert_agent(
        db,
        Agent(
            run_id=run.id,
            role=Role.FIXER,
            title="child",
            status="working",
        ),
    )
    db.commit()
    blocked = client.delete(f"/internal/db/tables/runs/rows/{run.id}")
    assert blocked.status_code == 409


def test_unknown_table_is_404(client: TestClient) -> None:
    assert client.get("/internal/db/tables/no_such_table/rows").status_code == 404
    assert (
        client.patch(
            "/internal/db/tables/no_such_table/rows/1",
            json={"values": {"x": "y"}},
        ).status_code
        == 404
    )
