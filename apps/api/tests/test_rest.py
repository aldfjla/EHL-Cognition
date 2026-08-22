"""REST surface: the shapes ``apps/ui/lib/api.ts`` reads.

Assertions are deliberately about field names and ordering, not counts: those
are the parts the dashboard breaks on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from orchestrator.schemas import (
    Agent,
    AgentStatus,
    Cluster,
    EventType,
    Finding,
    FindingKind,
    FindingStatus,
    Message,
    MessageKind,
    Report,
    Role,
    Run,
    Scenario,
    ScenarioStatus,
    Speaker,
    Stage,
    SuiteStats,
    Verdict,
)

from app.store import repo


def test_health_needs_nothing(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_reports_each_dependency(client: TestClient) -> None:
    body = client.get("/ready").json()

    assert body["status"] == "ok"
    checks = body["checks"]
    assert checks["database"] is True
    # No Devin key and no Menagerie checkout in the test environment.
    assert checks["devin_api_key"] is False
    assert checks["menagerie"] is False
    assert any("MENAGERIE_DIR" in warning for warning in checks["warnings"])


def test_runs_are_listed_newest_first(client: TestClient, db: Any) -> None:
    older = repo.create_run(
        db, Run(repo="acme/robot", branch="main", commit_sha="1" * 40)
    )
    newer = repo.create_run(
        db, Run(repo="acme/robot", branch="main", commit_sha="2" * 40)
    )
    db.commit()

    ids = [item["id"] for item in client.get("/runs").json()]

    assert set(ids) == {newer.id, older.id}
    assert ids.index(newer.id) <= ids.index(older.id)


def test_run_detail_includes_scenarios_and_clusters(
    client: TestClient, db: Any, run: Run
) -> None:
    """First paint is one round trip; the WebSocket keeps it current."""
    repo.upsert_scenario(db, Scenario(run_id=run.id, index=0, seed=11, label="grasp"))
    repo.upsert_cluster(db, Cluster(run_id=run.id, label="gripper early", size=3))
    db.commit()

    body = client.get(f"/runs/{run.id}").json()

    assert body["id"] == run.id
    assert body["stage"] == Stage.RUN_SUITE.value
    assert body["commit_sha"] == "a" * 40
    assert [item["label"] for item in body["scenarios"]] == ["grasp"]
    assert [item["label"] for item in body["clusters"]] == ["gripper early"]
    assert body["worker_pool"] == {
        "workers": 4,
        "busy": 0,
        "queued": 1,
        "reason": None,
        "running": [],
    }


def test_worker_pool_measures_scenarios_and_uses_latest_event(
    client: TestClient, db: Any, run: Run, bus: Any
) -> None:
    repo.upsert_scenario(
        db,
        Scenario(
            run_id=run.id,
            index=1,
            seed=11,
            status=ScenarioStatus.RUNNING,
            worker_id="worker-1",
        ),
    )
    repo.upsert_scenario(
        db,
        Scenario(run_id=run.id, index=0, seed=10, status=ScenarioStatus.PENDING),
    )
    db.commit()

    async def publish() -> None:
        await bus.emit(
            run.id,
            EventType.WORKER_POOL_CHANGED,
            {"workers": 8, "busy": 99, "queued": 99, "reason": "fan-out"},
        )

    asyncio.run(publish())
    body = client.get(f"/runs/{run.id}/workers").json()

    assert body["workers"] == 8
    assert body["busy"] == 1
    assert body["queued"] == 1
    assert body["reason"] == "fan-out"
    assert [item["index"] for item in body["running"]] == [1]


def test_worker_pool_falls_back_without_events(
    client: TestClient, db: Any, run: Run
) -> None:
    for index in (2, 0):
        repo.upsert_scenario(
            db,
            Scenario(
                run_id=run.id, index=index, seed=index, status=ScenarioStatus.RUNNING
            ),
        )
    db.commit()

    body = client.get(f"/runs/{run.id}/workers").json()

    assert body["workers"] == 4
    assert body["busy"] == 2
    assert body["queued"] == 0


def test_missing_run_is_404(client: TestClient) -> None:
    response = client.get("/runs/run_nope")

    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def test_scenarios_come_back_in_index_order(
    client: TestClient, db: Any, run: Run
) -> None:
    for index in (2, 0, 1):
        repo.upsert_scenario(
            db,
            Scenario(
                run_id=run.id,
                index=index,
                seed=1000 + index,
                label=f"grasp-{index}",
                status=ScenarioStatus.PASSED,
                live_frame_path="frames/live.jpg" if index == 0 else None,
                worker_id="worker-1" if index == 0 else None,
                progress=0.5 if index == 0 else None,
            ),
        )
    db.commit()

    indices = [item["index"] for item in client.get(f"/runs/{run.id}/scenarios").json()]

    assert indices == [0, 1, 2]
    scenario = client.get(f"/runs/{run.id}/scenarios").json()[0]
    assert scenario["live_frame_path"] == "frames/live.jpg"
    assert scenario["worker_id"] == "worker-1"
    assert scenario["progress"] == 0.5


def test_scenarios_filter_by_attempt(client: TestClient, db: Any, run: Run) -> None:
    """VERIFY re-runs share a seed with the baseline; ``attempt`` separates them."""
    repo.upsert_scenario(
        db, Scenario(run_id=run.id, index=0, seed=7, attempt=1, label="baseline")
    )
    repo.upsert_scenario(
        db, Scenario(run_id=run.id, index=0, seed=7, attempt=2, label="verify")
    )
    db.commit()

    verify = client.get(f"/runs/{run.id}/scenarios", params={"attempt": 2}).json()

    assert [item["label"] for item in verify] == ["verify"]


def test_scenario_upsert_is_idempotent_on_seed_and_attempt(
    client: TestClient, db: Any, run: Run
) -> None:
    """A redelivered scenario.finished updates the row instead of duplicating it."""
    scenario = Scenario(
        run_id=run.id, index=0, seed=7, label="grasp", status=ScenarioStatus.RUNNING
    )
    repo.upsert_scenario(db, scenario)
    repo.upsert_scenario(
        db, scenario.model_copy(update={"status": ScenarioStatus.FAILED})
    )
    db.commit()

    body = client.get(f"/runs/{run.id}/scenarios").json()

    assert len(body) == 1
    assert body[0]["status"] == ScenarioStatus.FAILED.value


def test_agents_messages_and_findings(client: TestClient, db: Any, run: Run) -> None:
    investigator = repo.upsert_agent(
        db,
        Agent(
            run_id=run.id,
            role=Role.INVESTIGATOR,
            title="investigator-1",
            status=AgentStatus.WORKING,
            desktop_url="https://desktop.example",
            issue="gripper closes early",
            step="reproduce",
        ),
    )
    fixer = repo.upsert_agent(
        db, Agent(run_id=run.id, role=Role.FIXER, title="fixer-1")
    )
    repo.add_message(
        db,
        Message(
            run_id=run.id,
            from_agent_id=investigator.id,
            from_role=Speaker.INVESTIGATOR,
            to_role=Speaker.ORCHESTRATOR,
            kind=MessageKind.FINDING,
            body="approach velocity is the cause",
        ),
    )
    repo.add_message(
        db,
        Message(
            run_id=run.id,
            from_agent_id=fixer.id,
            from_role=Speaker.FIXER,
            to_role=Speaker.ORCHESTRATOR,
            kind=MessageKind.HANDOFF,
            body="patched the controller",
        ),
    )
    repo.upsert_finding(
        db,
        Finding(
            run_id=run.id,
            author_agent_id=investigator.id,
            author_role=Speaker.INVESTIGATOR,
            kind=FindingKind.ROOT_CAUSE,
            summary="gripper closes early",
            status=FindingStatus.CONFIRMED,
        ),
    )
    db.commit()

    agents = client.get(f"/runs/{run.id}/agents").json()
    assert [item["title"] for item in agents] == ["investigator-1", "fixer-1"]
    assert agents[0]["desktop_url"] == "https://desktop.example"
    assert agents[0]["issue"] == "gripper closes early"
    assert agents[0]["step"] == "reproduce"

    assert (
        client.get(f"/agents/{investigator.id}").json()["role"]
        == Role.INVESTIGATOR.value
    )
    assert client.get("/agents/agt_nope").status_code == 404

    scoped = client.get(f"/agents/{investigator.id}/messages").json()
    assert [item["body"] for item in scoped] == ["approach velocity is the cause"]

    assert len(client.get(f"/runs/{run.id}/messages").json()) == 2

    findings = client.get(f"/runs/{run.id}/findings").json()
    assert findings[0]["summary"] == "gripper closes early"
    assert (
        client.get(
            f"/runs/{run.id}/findings",
            params={"status": FindingStatus.PROPOSED.value},
        ).json()
        == []
    )


def test_clusters_are_largest_first(client: TestClient, db: Any, run: Run) -> None:
    repo.upsert_cluster(
        db,
        Cluster(
            run_id=run.id, label="small", size=1, scenario_ids=["s1"], signature="a"
        ),
    )
    repo.upsert_cluster(
        db,
        Cluster(
            run_id=run.id,
            label="big",
            size=4,
            scenario_ids=["s2", "s3", "s4", "s5"],
            signature="b",
        ),
    )
    db.commit()

    labels = [item["label"] for item in client.get(f"/runs/{run.id}/clusters").json()]

    assert labels == ["big", "small"]


def test_report_round_trips_nested_json(client: TestClient, db: Any, run: Run) -> None:
    repo.save_report(
        db,
        Report(
            run_id=run.id,
            title="2 failures fixed",
            summary="approach velocity",
            verdict=Verdict.FIXED,
            before=SuiteStats(total=24, passed=19, failed=5),
            after=SuiteStats(total=24, passed=24, failed=0),
        ),
    )
    db.commit()

    body = client.get(f"/runs/{run.id}/report").json()

    assert body["verdict"] == Verdict.FIXED.value
    assert body["before"]["failed"] == 5
    assert body["after"]["passed"] == 24


def test_report_404_before_stage_report(client: TestClient, run: Run) -> None:
    assert client.get(f"/runs/{run.id}/report").status_code == 404


def test_ingested_events_are_replayable(client: TestClient, run: Run) -> None:
    """The event ingest path drives a live dashboard through."""
    accepted = client.post(
        f"/runs/{run.id}/events",
        json={
            "run_id": run.id,
            "type": "run.stage_changed",
            "data": {"to": Stage.RUN_SUITE.value},
        },
    )

    assert accepted.status_code == 202
    history = client.get(f"/runs/{run.id}/events").json()
    assert [item["type"] for item in history] == ["run.stage_changed"]
    assert history[0]["seq"] == 1
