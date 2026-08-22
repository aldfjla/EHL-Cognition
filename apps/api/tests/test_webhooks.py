"""Webhook intake: signatures, filtering, and run creation.

The signature check is the security boundary of the whole service, so it is
tested from both sides — a valid delivery must start a run and an invalid one
must not reach the store at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient
from orchestrator.schemas import Repo, Run, Stage

from app.routers.webhooks import verify_signature
from app.store import repo
from app.store.db import session_scope

SECRET = "shhh"


def push_payload(**overrides: Any) -> dict[str, Any]:
    """A minimal but realistically shaped GitHub push payload."""
    payload: dict[str, Any] = {
        "ref": "refs/heads/main",
        "after": "b" * 40,
        "repository": {"full_name": "acme/robot"},
        "pusher": {"name": "ada"},
        "head_commit": {"id": "b" * 40, "message": "tune the grasp"},
    }
    payload.update(overrides)
    return payload


def signed(
    payload: dict[str, Any], secret: str = SECRET
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }


def test_verify_signature_accepts_and_rejects() -> None:
    body = b'{"ref":"refs/heads/main"}'
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(body, f"sha256={digest}", SECRET)
    assert not verify_signature(body, f"sha256={digest}", "other")
    assert not verify_signature(body + b" ", f"sha256={digest}", SECRET)
    assert not verify_signature(body, digest, SECRET)  # missing sha256= prefix
    assert not verify_signature(body, "", SECRET)
    assert not verify_signature(body, f"sha256={digest}", "")


def test_valid_push_creates_a_run(client: TestClient) -> None:
    body, headers = signed(push_payload())

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert response.json()["dashboard_url"].endswith(f"/runs/{run_id}")

    with session_scope() as db:
        run = repo.get_run(db, run_id)
    assert run is not None
    assert run.repo == "acme/robot"
    assert run.commit_sha == "b" * 40
    assert run.pushed_by == "ada"


def test_bad_signature_is_rejected(client: TestClient) -> None:
    body, headers = signed(push_payload(), secret="wrong")

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 401
    with session_scope() as db:
        assert repo.list_runs(db) == []


def test_missing_signature_is_rejected(client: TestClient) -> None:
    response = client.post("/webhooks/github", json=push_payload())

    assert response.status_code == 401


def test_other_branch_is_ignored_with_200(client: TestClient) -> None:
    """A deliberate skip is not an error — GitHub's delivery page must stay green."""
    body, headers = signed(push_payload(ref="refs/heads/spike"))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    assert "TARGET_BRANCH" in response.json()["ignored"]


def test_other_repo_is_ignored(client: TestClient) -> None:
    body, headers = signed(push_payload(repository={"full_name": "other/repo"}))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert "TARGET_REPO" in response.json()["ignored"]


def test_branch_deletion_is_ignored(client: TestClient) -> None:
    body, headers = signed(push_payload(deleted=True))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.json()["ignored"] == "branch deletion"


def test_non_push_event_is_ignored(client: TestClient) -> None:
    body, headers = signed(push_payload())
    headers["X-GitHub-Event"] = "ping"

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert "not a push" in response.json()["ignored"]


def test_redelivery_reuses_the_in_flight_run(client: TestClient, db: Any) -> None:
    """GitHub redelivers; two pipelines on one commit would fight over branches."""
    active = repo.create_run(
        db,
        Run(
            repo="acme/robot",
            branch="main",
            commit_sha="b" * 40,
            stage=Stage.RUN_SUITE,
        ),
    )
    db.commit()
    body, headers = signed(push_payload())

    response = client.post("/webhooks/github", content=body, headers=headers).json()

    assert response["run_id"] == active.id
    assert "already in flight" in response["ignored"]
    with session_scope() as check:
        assert len(repo.list_runs(check)) == 1


def test_manual_trigger_accepts_json_and_form(client: TestClient) -> None:
    from_json = client.post(
        "/webhooks/manual", json={"repo": "acme/robot", "sha": "c" * 40}
    )
    from_form = client.post(
        "/webhooks/manual", data={"repo": "acme/robot", "sha": "d" * 40}
    )

    assert from_json.status_code == 200
    assert from_form.status_code == 200
    assert from_json.json()["run_id"] != from_form.json()["run_id"]


def test_manual_trigger_requires_a_sha(client: TestClient) -> None:
    assert (
        client.post("/webhooks/manual", json={"repo": "acme/robot"}).status_code == 422
    )


def test_run_created_is_published(client: TestClient, bus: Any) -> None:
    body, headers = signed(push_payload())

    run_id = client.post("/webhooks/github", content=body, headers=headers).json()[
        "run_id"
    ]

    types = [event.type.value for event in bus.history(run_id)]
    assert types[0] == "run.created"
    # The index topic mirrors run-level events for the dashboard's home page.
    assert "run.created" in [event.type.value for event in bus.history("*")]


def test_connected_repo_push_uses_branch_and_suite_size(
    client: TestClient, db: Any, monkeypatch: Any
) -> None:
    connected = repo.create_repo(
        db, Repo(full_name="acme/robot", branch="develop", suite_size=7)
    )
    db.commit()
    captured: dict[str, Any] = {}

    async def fake_drive(
        run_id: str, bus: Any, settings: Any, suite_size: int | None = None
    ) -> None:
        captured.update(run_id=run_id, suite_size=suite_size)

    monkeypatch.setattr("app.routers.webhooks._drive_pipeline", fake_drive)
    body, headers = signed(
        push_payload(ref="refs/heads/develop", repository={"full_name": "acme/robot"})
    )

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    assert captured["suite_size"] == 7
    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
        assert stored is not None
        assert stored.last_push_at is not None


def test_connected_repo_rejects_a_different_branch(client: TestClient, db: Any) -> None:
    repo.create_repo(db, Repo(full_name="acme/robot", branch="develop"))
    db.commit()
    body, headers = signed(push_payload(ref="refs/heads/main"))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    assert "connected branch develop" in response.json()["ignored"]
    with session_scope() as check:
        assert repo.list_runs(check) == []
