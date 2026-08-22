"""Connected repository CRUD and status derivation."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from orchestrator.schemas import Repo, Run, Stage

from app.store import repo


def test_connect_list_update_and_delete_repo(client: TestClient, db: Any) -> None:
    response = client.post(
        "/repos",
        json={"full_name": "acme/arm", "branch": "trunk", "suite_size": 12},
    )

    assert response.status_code == 201
    body = response.json()
    connected = body["repo"]
    assert connected["full_name"] == "acme/arm"
    assert connected["branch"] == "trunk"
    assert connected["suite_size"] == 12
    assert connected["status"] == "dormant"
    assert connected["latest_run"] is None
    assert body["webhook"] == {
        "url": "http://localhost:8000/webhooks/github",
        "secret_configured": True,
    }

    listed = client.get("/repos")
    assert listed.status_code == 200
    assert listed.json() == [connected]

    updated = client.patch(
        f"/repos/{connected['id']}",
        json={"branch": "main", "suite_size": 50},
    )
    assert updated.status_code == 200
    assert updated.json()["branch"] == "main"
    assert updated.json()["suite_size"] == 50

    deleted = client.delete(f"/repos/{connected['id']}")
    assert deleted.status_code == 204
    assert client.get("/repos").json() == []


def test_connect_repo_validates_shape_and_duplicates(client: TestClient) -> None:
    assert client.post("/repos", json={"full_name": "not-a-name"}).status_code == 422

    assert client.post("/repos", json={"full_name": "acme/arm"}).status_code == 201
    duplicate = client.post("/repos", json={"full_name": "acme/arm"})
    assert duplicate.status_code == 409


def test_repo_status_and_latest_run_come_from_history(
    client: TestClient, db: Any
) -> None:
    connected = repo.create_repo(db, Repo(full_name="acme/arm"))
    repo.create_run(
        db,
        Run(
            repo="acme/arm",
            branch="main",
            commit_sha="a" * 40,
            stage=Stage.PASSED_CLEAN,
        ),
    )
    latest = repo.create_run(
        db,
        Run(
            repo="acme/arm",
            branch="main",
            commit_sha="b" * 40,
            stage=Stage.RUN_SUITE,
        ),
    )
    db.commit()

    listed = client.get("/repos").json()

    assert len(listed) == 1
    assert listed[0]["id"] == connected.id
    assert listed[0]["status"] == "running"
    assert listed[0]["latest_run"] == {
        "id": latest.id,
        "stage": Stage.RUN_SUITE.value,
        "created_at": latest.created_at.isoformat().replace("+00:00", "Z"),
    }
