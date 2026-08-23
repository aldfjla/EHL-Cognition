"""Webhook intake: signatures, filtering, and run creation.

The signature check is the security boundary of the whole service, so it is
tested from both sides — a valid delivery must start a run and an invalid one
must not reach the store at all.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from orchestrator.github import GitHubError
from orchestrator.schemas import Agent, AgentStatus, EventType, Repo, Role, Run, Stage

from app.routers.webhooks import _drive_pipeline, _persist_run_events, verify_signature
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
    body_json = response.json()
    assert body_json["reason_code"] == "branch_not_watched"
    assert "spike is not watched" in body_json["ignored"]
    assert body_json["filters"]["branches"] == ["main"]


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


def test_manual_trigger_resolves_connected_branch_head(
    client: TestClient, db: Any, monkeypatch: Any
) -> None:
    connected = repo.create_repo(
        db, Repo(full_name="acme/robot", branch="develop", suite_size=7)
    )
    db.commit()
    captured: dict[str, Any] = {}

    async def resolve(repo_name: str, branch: str) -> tuple[str, str]:
        captured.update(repo_name=repo_name, branch=branch)
        return "e" * 40, "resolve the grasp"

    async def fake_drive(
        run_id: str, bus: Any, settings: Any, suite_size: int | None = None
    ) -> None:
        captured.update(run_id=run_id, suite_size=suite_size)

    monkeypatch.setattr("app.routers.webhooks.resolve_branch_head", resolve)
    monkeypatch.setattr("app.routers.webhooks._drive_pipeline", fake_drive)

    response = client.post("/webhooks/manual", json={"repo": "acme/robot"})

    assert response.status_code == 200
    assert captured == {
        "repo_name": "acme/robot",
        "branch": "develop",
        "run_id": response.json()["run_id"],
        "suite_size": 7,
    }
    with session_scope() as check:
        stored = repo.get_run(check, response.json()["run_id"])
    assert stored is not None
    assert stored.commit_sha == "e" * 40
    assert stored.commit_message == "resolve the grasp"
    assert stored.branch == "develop"
    assert stored.pushed_by == "manual"
    assert connected.full_name == "acme/robot"


def test_manual_trigger_explicit_sha_skips_github_resolution(
    client: TestClient, monkeypatch: Any
) -> None:
    async def explode(*args: Any) -> tuple[str, str]:
        raise AssertionError("explicit SHA must not resolve branch HEAD")

    monkeypatch.setattr("app.routers.webhooks.resolve_branch_head", explode)

    response = client.post(
        "/webhooks/manual",
        json={"repo": "acme/robot", "sha": "c" * 40, "branch": "release"},
    )

    assert response.status_code == 200
    with session_scope() as check:
        stored = repo.get_run(check, response.json()["run_id"])
    assert stored is not None
    assert stored.commit_sha == "c" * 40
    assert stored.commit_message == "manual trigger"
    assert stored.branch == "release"
    assert stored.pushed_by == "manual"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        ("token is unavailable", 422),
        ("the branch does not exist", 404),
        ("GitHub API unavailable; try again", 502),
    ],
)
def test_manual_trigger_maps_github_errors(
    client: TestClient, monkeypatch: Any, error: str, status_code: int
) -> None:
    async def fail(*args: Any) -> tuple[str, str]:
        raise GitHubError(error, status_code=status_code)

    monkeypatch.setattr("app.routers.webhooks.resolve_branch_head", fail)

    response = client.post("/webhooks/manual", json={"repo": "acme/robot"})

    assert response.status_code == status_code
    assert response.json()["detail"] == error


def test_run_created_is_published(client: TestClient, bus: Any) -> None:
    body, headers = signed(push_payload())

    run_id = client.post("/webhooks/github", content=body, headers=headers).json()[
        "run_id"
    ]

    types = [event.type.value for event in bus.history(run_id)]
    assert types[0] == "run.created"
    # The index topic mirrors run-level events for the dashboard's home page.
    assert "run.created" in [event.type.value for event in bus.history("*")]


async def test_pipeline_error_reaches_existing_subscriber(
    run: Run, bus: Any, settings: Any, monkeypatch: Any
) -> None:
    """Fatal errors publish before the run stream is closed."""

    class FailingPipeline:
        def __init__(self, _ctx: Any) -> None:
            pass

        async def run(self) -> None:
            raise RuntimeError("pipeline exploded")

    seen: list[Any] = []

    async def collect() -> None:
        async for event in bus.subscribe(run.id):
            seen.append(event)
            if event.type is EventType.ERROR:
                return

    subscriber = asyncio.create_task(collect())
    await asyncio.sleep(0)
    monkeypatch.setattr("app.routers.webhooks.Pipeline", FailingPipeline)

    await _drive_pipeline(run.id, bus, settings)
    await asyncio.wait_for(subscriber, timeout=1)

    assert any(
        event.type is EventType.ERROR
        and event.data["message"] == "pipeline exploded"
        and event.data["fatal"] is True
        for event in seen
    )


async def test_run_events_are_mirrored_into_rest_store(run: Run, bus: Any) -> None:
    """Stage, fatal errors and agent lifecycle events populate REST state."""
    persistence = asyncio.create_task(_persist_run_events(run.id, bus))
    await asyncio.sleep(0)

    await bus.emit(
        run.id,
        EventType.RUN_STAGE_CHANGED,
        {"stage": Stage.FIX.value, "previous_stage": Stage.RUN_SUITE.value},
    )
    await bus.emit(
        run.id,
        EventType.ERROR,
        {"stage": Stage.FIX.value, "message": "agent unavailable", "fatal": True},
    )
    agent = Agent(
        run_id=run.id,
        role=Role.INVESTIGATOR,
        title="Trace",
        task="find the issue",
        status=AgentStatus.STARTING,
    )
    await bus.emit(run.id, EventType.AGENT_CREATED, agent.model_dump(mode="json"))
    await bus.emit(
        run.id,
        EventType.AGENT_STATUS_CHANGED,
        {
            "agent_id": agent.id,
            "status": AgentStatus.WORKING.value,
            "session_id": "devin-1",
            "session_url": "https://app.devin.ai/sessions/devin-1",
        },
    )
    await bus.emit(
        run.id,
        EventType.AGENT_UPDATED,
        {
            "agent_id": agent.id,
            "desktop_url": "https://desktop.example/1",
            "issue": "measured timeout",
            "step": "investigating",
        },
    )
    await bus.emit(
        run.id,
        EventType.AGENT_ACTIVITY,
        {"agent_id": agent.id, "text": "reading the trace"},
    )
    await bus.emit(
        run.id,
        EventType.AGENT_STATUS_CHANGED,
        {
            "agent_id": agent.id,
            "status": AgentStatus.SUCCEEDED.value,
            "finished_at": "2026-01-01T00:00:00+00:00",
        },
    )
    await bus.close(run.id)
    await asyncio.wait_for(persistence, timeout=1)

    with session_scope() as db:
        stored_run = repo.get_run(db, run.id)
        stored_agents = repo.list_agents(db, run.id)

    assert stored_run is not None
    assert stored_run.stage is Stage.FIX
    assert stored_run.error == "agent unavailable"
    assert len(stored_agents) == 1
    stored = stored_agents[0]
    assert stored.status is AgentStatus.SUCCEEDED
    assert stored.session_id == "devin-1"
    assert stored.desktop_url == "https://desktop.example/1"
    assert stored.issue == "measured timeout"
    assert stored.step == "investigating"
    assert stored.last_activity == "reading the trace"
    assert stored.finished_at is not None


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


def commits(*paths: str) -> list[dict[str, Any]]:
    """A commit list shaped like GitHub's, touching ``paths``."""
    return [{"id": "b" * 40, "added": list(paths), "removed": [], "modified": []}]


def test_docs_only_push_is_ignored_with_a_reason_code(client: TestClient) -> None:
    """A README-only push must not burn a run."""
    body, headers = signed(push_payload(commits=commits("README.md", "docs/x.md")))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["reason_code"] == "no_matching_paths"
    assert "README.md" in payload["ignored"]
    assert payload["filters"]["path_include"]
    with session_scope() as check:
        assert repo.list_runs(check) == []


def test_workflow_only_push_is_ignored(client: TestClient) -> None:
    """The default ``.github/*`` exclusion must actually match, dot included."""
    body, headers = signed(
        push_payload(commits=commits(".github/workflows/ci.yml", "README.md"))
    )

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["reason_code"] == "no_matching_paths"
    with session_scope() as check:
        assert repo.list_runs(check) == []


def test_dot_prefixed_include_starts_a_run(client: TestClient, db: Any) -> None:
    """A repo that watches ``.env`` keeps its dot through the round-trip."""
    repo.create_repo(
        db,
        Repo(
            full_name="acme/robot",
            branch="main",
            path_include=[".env", ".config/*"],
            path_exclude=[],
            filters_source="robotci.yaml",
        ),
    )
    db.commit()

    payload = client.post(
        "/webhooks/github", **_signed_kwargs(push_payload(commits=commits(".env")))
    ).json()

    assert payload["reason_code"] == "started"
    assert payload["matched_paths"] == [".env"]


def test_stored_empty_exclude_survives_the_round_trip(
    client: TestClient, db: Any
) -> None:
    """``paths.exclude: []`` disables the defaults; storage must not undo it."""
    from app.routers.webhooks import _filter_cache

    connected = repo.create_repo(db, Repo(full_name="acme/robot", branch="main"))
    db.commit()

    asyncio.run(
        _filter_cache("acme/robot")(
            {"ci": {"paths": {"include": ["docs/*.yaml"], "exclude": []}}}
        )
    )

    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
    assert stored is not None
    assert stored.path_exclude == []

    payload = client.post(
        "/webhooks/github",
        **_signed_kwargs(push_payload(commits=commits("docs/tuning.yaml"))),
    ).json()

    assert payload["reason_code"] == "started"
    assert payload["matched_paths"] == ["docs/tuning.yaml"]


def test_unset_path_filters_read_back_as_defaults(client: TestClient, db: Any) -> None:
    connected = repo.create_repo(db, Repo(full_name="acme/robot", branch="main"))
    db.commit()

    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
    assert stored is not None
    assert stored.path_include is None
    assert stored.path_exclude is None

    payload = client.post(
        "/webhooks/github",
        **_signed_kwargs(push_payload(commits=commits("docs/tuning.yaml"))),
    ).json()

    assert payload["reason_code"] == "no_matching_paths"


def test_control_code_push_starts_a_run_and_reports_the_match(
    client: TestClient,
) -> None:
    body, headers = signed(
        push_payload(commits=commits("src/controller.py", "README.md"))
    )

    payload = client.post("/webhooks/github", content=body, headers=headers).json()

    assert payload["reason_code"] == "started"
    assert payload["matched_paths"] == ["src/controller.py"]
    assert "run_id" in payload


def test_push_without_file_lists_starts_a_run_and_says_so(client: TestClient) -> None:
    """We never skip CI on a push whose contents the delivery did not include."""
    body, headers = signed(push_payload(commits=[{"id": "b" * 40}]))

    payload = client.post("/webhooks/github", content=body, headers=headers).json()

    assert payload["reason_code"] == "changed_paths_unavailable"
    assert "path filters were not applied" in payload["reason"]
    assert "run_id" in payload


def test_connected_repo_path_filters_are_applied(client: TestClient, db: Any) -> None:
    repo.create_repo(
        db,
        Repo(
            full_name="acme/robot",
            branch="main",
            path_include=["control/*"],
            path_exclude=["control/experiments/*"],
            filters_source="robotci.yaml",
        ),
    )
    db.commit()

    ignored = client.post(
        "/webhooks/github",
        **_signed_kwargs(push_payload(commits=commits("control/experiments/a.py"))),
    ).json()
    started = client.post(
        "/webhooks/github",
        **_signed_kwargs(push_payload(commits=commits("control/arm.py"))),
    ).json()

    assert ignored["reason_code"] == "no_matching_paths"
    assert ignored["filters"]["path_include"] == ["control/*"]
    assert started["matched_paths"] == ["control/arm.py"]


def test_connected_repo_extra_branch_patterns_are_watched(
    client: TestClient, db: Any
) -> None:
    repo.create_repo(
        db, Repo(full_name="acme/robot", branch="main", branches=["release/*"])
    )
    db.commit()
    body, headers = signed(push_payload(ref="refs/heads/release/2.1"))

    payload = client.post("/webhooks/github", content=body, headers=headers).json()

    assert "run_id" in payload
    assert payload["reason_code"] == "changed_paths_unavailable"


def test_ignored_push_does_not_touch_last_push_at(client: TestClient, db: Any) -> None:
    """``last_push_at`` means "we ran for a push", not "GitHub called us"."""
    connected = repo.create_repo(db, Repo(full_name="acme/robot", branch="main"))
    db.commit()
    body, headers = signed(push_payload(commits=commits("README.md")))

    client.post("/webhooks/github", content=body, headers=headers)

    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
        assert stored is not None
        assert stored.last_push_at is None


def test_filters_are_cached_from_the_checkout(client: TestClient, db: Any) -> None:
    """Stage TRIGGERED is the first place the repo's own ``ci:`` block exists."""
    from app.routers.webhooks import _filter_cache

    connected = repo.create_repo(db, Repo(full_name="acme/robot", branch="main"))
    db.commit()

    asyncio.run(
        _filter_cache("acme/robot")(
            {"ci": {"branches": ["dev", "release/*"], "paths": {"include": ["ctrl/*"]}}}
        )
    )

    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
    assert stored is not None
    assert stored.branch == "dev"
    assert stored.branches == ["release/*"]
    assert stored.path_include == ["ctrl/*"]
    assert stored.filters_source == "robotci.yaml"


def test_filters_are_left_alone_without_a_ci_section(
    client: TestClient, db: Any
) -> None:
    from app.routers.webhooks import _filter_cache

    connected = repo.create_repo(
        db, Repo(full_name="acme/robot", branch="main", path_include=["ctrl/*"])
    )
    db.commit()

    asyncio.run(_filter_cache("acme/robot")({"control": {"entrypoint": "a.py:run"}}))

    with session_scope() as check:
        stored = repo.get_repo(check, connected.id)
    assert stored is not None
    assert stored.path_include == ["ctrl/*"]


def _signed_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    body, headers = signed(payload)
    return {"content": body, "headers": headers}


def test_connected_repo_rejects_a_different_branch(client: TestClient, db: Any) -> None:
    repo.create_repo(db, Repo(full_name="acme/robot", branch="develop"))
    db.commit()
    body, headers = signed(push_payload(ref="refs/heads/main"))

    response = client.post("/webhooks/github", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["reason_code"] == "branch_not_watched"
    assert "watching: develop" in response.json()["ignored"]
    with session_scope() as check:
        assert repo.list_runs(check) == []
