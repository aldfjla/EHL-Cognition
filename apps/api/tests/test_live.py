"""Live JPEG and MJPEG endpoint behavior.

The feed is a side channel to the event stream: it must serve complete
changed frames, respect the artifact containment boundary, and release its
capacity when a consumer stops reading.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from orchestrator.schemas import Scenario, ScenarioStatus

from app.config import get_settings
from app.routers.live import _mjpeg, live_stream_limiter
from app.store import repo

JPEG = b"\xff\xd8minimal-frame\xff\xd9"


@pytest.fixture(autouse=True)
def reset_stream_limiter() -> Any:
    """Prevent one aborted test from affecting the next stream assertion."""
    live_stream_limiter.reset()
    yield
    live_stream_limiter.reset()


def make_scenario(db: Any, run_id: str, **changes: Any) -> Scenario:
    """Persist one scenario with the requested live-feed state."""
    scenario = Scenario(run_id=run_id, index=0, seed=99, **changes)
    result = repo.upsert_scenario(db, scenario)
    db.commit()
    return result


def write_frame(rel_path: str, content: bytes = JPEG) -> Path:
    """Write a frame beneath the configured artifact root."""
    path = get_settings().artifacts_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_live_jpg_serves_uncached_current_frame(
    client: TestClient, db: Any, run: Any
) -> None:
    scenario = make_scenario(
        db, run.id, live_frame_path="frames/live.jpg", status=ScenarioStatus.RUNNING
    )
    assert scenario.live_frame_path is not None
    write_frame(scenario.live_frame_path)

    response = client.get(f"/runs/{run.id}/scenarios/{scenario.id}/live.jpg")

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("endpoint", ["live.jpg", "live.mjpg"])
def test_missing_live_frame_is_an_immediate_404(
    client: TestClient, db: Any, run: Any, endpoint: str
) -> None:
    scenario = make_scenario(db, run.id)

    response = client.get(
        f"/runs/{run.id}/scenarios/{scenario.id}/{endpoint}",
    )

    assert response.status_code == 404
    assert "no live frame yet" in response.json()["detail"]


def test_recorded_but_missing_frame_is_404_for_jpg(
    client: TestClient, db: Any, run: Any
) -> None:
    scenario = make_scenario(
        db,
        run.id,
        live_frame_path="frames/not-created.jpg",
        status=ScenarioStatus.RUNNING,
    )

    response = client.get(f"/runs/{run.id}/scenarios/{scenario.id}/live.jpg")

    assert response.status_code == 404


def test_live_endpoints_reject_wrong_run(client: TestClient, db: Any, run: Any) -> None:
    scenario = make_scenario(
        db, run.id, live_frame_path="frames/live.jpg", status=ScenarioStatus.RUNNING
    )
    assert scenario.live_frame_path is not None
    write_frame(scenario.live_frame_path)

    response = client.get(
        f"/runs/wrong-run/scenarios/{scenario.id}/live.jpg",
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "run not found"


def test_live_endpoints_reject_scenario_owned_by_another_run(
    client: TestClient, db: Any, run: Any
) -> None:
    other_run = repo.create_run(
        db, run.model_copy(update={"id": "run_other", "commit_sha": "b" * 40})
    )
    scenario = make_scenario(
        db,
        other_run.id,
        live_frame_path="frames/live.jpg",
        status=ScenarioStatus.RUNNING,
    )

    response = client.get(f"/runs/{run.id}/scenarios/{scenario.id}/live.jpg")

    assert response.status_code == 404
    assert response.json()["detail"] == "scenario not found"


def test_live_frame_path_cannot_escape_artifacts(
    client: TestClient, db: Any, run: Any
) -> None:
    scenario = make_scenario(
        db,
        run.id,
        live_frame_path="../secret.env",
        status=ScenarioStatus.RUNNING,
    )
    (get_settings().artifacts_dir.parent / "secret.env").write_text("KEY=1")

    response = client.get(f"/runs/{run.id}/scenarios/{scenario.id}/live.jpg")

    assert response.status_code == 400
    assert "KEY=1" not in response.text


def test_mjpeg_multipart_frame_and_finished_termination(
    client: TestClient, db: Any, run: Any
) -> None:
    scenario = make_scenario(
        db,
        run.id,
        live_frame_path="frames/live.jpg",
        status=ScenarioStatus.RUNNING,
    )
    assert scenario.live_frame_path is not None
    path = write_frame(scenario.live_frame_path)
    settings = get_settings()
    settings.live_stream_idle_timeout_s = 1.0

    async def consume() -> list[bytes]:
        parts: list[bytes] = []
        generator = _mjpeg(scenario.id, path, limiter=live_stream_limiter)
        try:
            parts.append(await anext(generator))
            repo.upsert_scenario(
                db, scenario.model_copy(update={"status": ScenarioStatus.FAILED})
            )
            db.commit()
            await asyncio.sleep(1.05)
            with pytest.raises(StopAsyncIteration):
                await anext(generator)
        finally:
            await generator.aclose()
        return parts

    assert live_stream_limiter.acquire()
    parts = asyncio.run(consume())
    assert parts[0].startswith(b"--frame\r\nContent-Type: image/jpeg\r\n")
    assert b"Content-Length: " in parts[0]
    assert parts[0].endswith(JPEG + b"\r\n")
    assert live_stream_limiter.active_count == 0


def test_mjpeg_close_releases_limiter_slot(db: Any, run: Any) -> None:
    scenario = make_scenario(
        db,
        run.id,
        live_frame_path="frames/live.jpg",
        status=ScenarioStatus.RUNNING,
    )
    assert scenario.live_frame_path is not None
    path = write_frame(scenario.live_frame_path)
    settings = get_settings()
    settings.live_stream_idle_timeout_s = 10.0

    async def abort() -> None:
        generator = _mjpeg(scenario.id, path, limiter=live_stream_limiter)
        await anext(generator)
        await generator.aclose()

    assert live_stream_limiter.acquire()
    asyncio.run(abort())
    assert live_stream_limiter.active_count == 0


def test_mjpeg_cap_sheds_with_retry_after(
    client: TestClient, db: Any, run: Any
) -> None:
    scenario = make_scenario(
        db,
        run.id,
        live_frame_path="frames/live.jpg",
        status=ScenarioStatus.RUNNING,
    )
    assert scenario.live_frame_path is not None
    write_frame(scenario.live_frame_path)
    get_settings().max_live_streams = 0

    response = client.get(f"/runs/{run.id}/scenarios/{scenario.id}/live.mjpg")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "capacity" in response.json()["detail"]
