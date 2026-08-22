"""Shared live pool concurrency, ordering, and side-channel behaviour."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from orchestrator.bus import EventBus
from orchestrator.pool import SuitePool, _normalize_spec, _scenario_kwargs
from orchestrator.schemas import EventType
from simkit.live import live_frame_path
from simkit.runner import run_scenario


def specs(count: int, *, start: int = 0) -> list[dict[str, int | str]]:
    return [
        {"id": f"s{index}", "index": index, "seed": index}
        for index in range(start, start + count)
    ]


class FakeRunner:
    def __init__(self, *, delay: float = 0.01, fail: set[int] | None = None) -> None:
        self.delay = delay
        self.fail = fail or set()
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, *, scenario_id: str, seed: int, record: object, **_: object):
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay)
            if seed in self.fail:
                raise RuntimeError("boom")
            return SimpleNamespace(
                scenario_id=scenario_id,
                seed=seed,
                status="failed" if seed == 1 else "passed",
                duration_s=self.delay,
                sim_time_s=1.0,
                criteria=[],
                diagnosis=None,
                video_path=str(record) if record else None,
                trace_path=None,
                error=None,
            )
        finally:
            with self._lock:
                self.in_flight -= 1


async def test_pool_bounds_concurrency_and_reuses_slots(tmp_path: Path) -> None:
    bus = EventBus()
    runner = FakeRunner(delay=0.01)
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=3,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        results = await pool.submit(
            specs(20), model_path="model", harness_path="harness", task={}
        )
    finally:
        await pool.aclose()

    started = [
        event
        for event in bus.history("run")
        if event.type is EventType.SCENARIO_STARTED
    ]
    assert len(results) == 20
    assert runner.max_in_flight == 3
    assert {event.data["worker_id"] for event in started} <= {"w0", "w1", "w2"}
    assert len({event.data["worker_id"] for event in started}) == 3


async def test_concurrent_submits_share_one_pool_without_batch_barrier(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    started_small = asyncio.Event()
    runner_calls: list[str] = []

    def runner(*, scenario_id: str, **_: object):
        runner_calls.append(scenario_id)
        if scenario_id == "small":
            started_small.set()
        time.sleep(0.025 if scenario_id.startswith("big") else 0.001)
        return SimpleNamespace(
            scenario_id=scenario_id,
            seed=0,
            status="passed",
            duration_s=0.0,
            sim_time_s=0.0,
            criteria=[],
            diagnosis=None,
            video_path=None,
            trace_path=None,
            error=None,
        )

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=2,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    big = [{"id": f"big{i}", "index": i, "seed": i} for i in range(8)]
    small = [{"id": "small", "index": 0, "seed": 99}]
    try:
        big_task = asyncio.create_task(
            pool.submit(big, model_path="m", harness_path="h", task={})
        )
        await asyncio.sleep(0.005)
        small_task = asyncio.create_task(
            pool.submit(small, model_path="m", harness_path="h", task={})
        )
        await asyncio.wait_for(started_small.wait(), timeout=0.2)
        assert not big_task.done()
        await small_task
        await big_task
    finally:
        await pool.aclose()

    assert runner_calls.index("small") < len(runner_calls) - 1


async def test_results_are_ordered_and_runner_errors_are_isolated(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    runner = FakeRunner(delay=0.01, fail={2})
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=3,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        result = await pool.submit(
            list(reversed(specs(5))),
            model_path="m",
            harness_path="h",
            task={},
        )
    finally:
        await pool.aclose()

    assert [item.scenario_id for item in result] == ["s0", "s1", "s2", "s3", "s4"]
    assert result[2].status == "error"
    assert "worker died: RuntimeError: boom" == result[2].error
    assert [item.status for item in result[:2]] == ["passed", "failed"]


async def test_failure_recording_replays_once_and_emits_artifact(
    tmp_path: Path,
) -> None:
    bus = EventBus()
    runner = FakeRunner(delay=0.001)
    callbacks: list[str] = []
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        result = await pool.submit(
            [{"id": "s1", "index": 0, "seed": 1}],
            model_path="m",
            harness_path="h",
            task={},
            on_result=lambda item: callbacks.append(item.scenario_id),
        )
    finally:
        await pool.aclose()

    artifacts = [
        event
        for event in bus.history("run")
        if event.type is EventType.ARTIFACT_CREATED
    ]
    assert result[0].status == "failed"
    assert result[0].video_path == str(tmp_path / "s1.mp4")
    assert callbacks == ["s1"]
    assert len(artifacts) == 1
    assert artifacts[0].data["path"] == "s1.mp4"


async def test_progress_watcher_emits_only_changed_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = EventBus()
    live = tmp_path / "live"
    live.mkdir()
    progress_interval = "0.02"
    runner_started = threading.Event()

    def runner(*, live: bool, progress_path: str, **_: object):
        runner_started.set()
        assert live
        frame_path = tmp_path / live_frame_path("s0")
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        frame_path.write_bytes(b"one")
        Path(progress_path).write_text(json.dumps({"progress": 0.4, "sim_time_s": 1.2}))
        time.sleep(0.03)
        frame_path.write_bytes(b"two")
        time.sleep(0.03)
        return SimpleNamespace(
            scenario_id="s0",
            seed=0,
            status="passed",
            duration_s=0.0,
            sim_time_s=0.0,
            criteria=[],
            diagnosis=None,
            video_path=None,
            trace_path=None,
            error=None,
        )

    monkeypatch.setenv("SCENARIO_PROGRESS_INTERVAL_S", progress_interval)
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        await pool.submit(
            [{"id": "s0", "index": 0, "seed": 0}],
            model_path="m",
            harness_path="h",
            task={},
        )
    finally:
        await pool.aclose()

    events = [
        event
        for event in bus.history("run")
        if event.type is EventType.SCENARIO_PROGRESS
    ]
    assert len(events) <= 2
    assert events[0].data["live_frame_path"] == "live/s0.jpg"
    assert any(event.data["progress"] == pytest.approx(0.4) for event in events)
    assert any(event.data["sim_time_s"] == pytest.approx(1.2) for event in events)
    assert runner_started.is_set()


def test_runner_kwargs_match_live_runner_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIMKIT_LIVE_FRAMES", "1")
    spec = _normalize_spec({"id": "s0", "index": 0, "seed": 0}, 0)
    kwargs = _scenario_kwargs(
        tmp_path,
        spec,
        model_path="m",
        harness_path="h",
        task={},
        record=False,
        worker_id="w0",
    )
    accepted = set(inspect.signature(run_scenario).parameters)
    assert set(kwargs) <= accepted
    assert kwargs["live"] is True
    assert kwargs["worker_id"] == "w0"
    assert kwargs["progress_path"] == str(tmp_path / "live/s0.progress.json")


async def test_real_runner_through_suite_pool_emits_live_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("SIMKIT_LIVE_FRAMES", "1")
    monkeypatch.setenv("SIMKIT_LIVE_FPS", "30")
    model = tmp_path / "toy.xml"
    model.write_text(
        """<mujoco model="toy">
  <option timestep="0.002"/>
  <worldbody><body><joint name="hinge" type="hinge" axis="0 1 0"/>
    <geom type="capsule" fromto="0 0 0 0.2 0 0" size="0.03"/>
  </body></worldbody>
  <actuator><position joint="hinge" kp="20"/></actuator>
</mujoco>"""
    )
    harness = tmp_path / "harness.py"
    harness.write_text(
        """import time

def run_episode(model, data, params):
    for _ in range(20):
        params["step"]()
        time.sleep(0.03)
"""
    )
    bus = EventBus()
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=2,
        artifacts_dir=tmp_path,
    )
    try:
        results = await pool.submit(
            specs(2),
            model_path=str(model),
            harness_path=str(harness),
            task={"rate_hz": 20, "success": [{"id": "within_time", "limit_s": 1}]},
        )
    finally:
        await pool.aclose()

    progress = [
        event
        for event in bus.history("run")
        if event.type is EventType.SCENARIO_PROGRESS
    ]
    started = [
        event
        for event in bus.history("run")
        if event.type is EventType.SCENARIO_STARTED
    ]
    assert len(results) == 2
    assert {event.data["worker_id"] for event in started} == {"w0", "w1"}
    assert progress
    assert {event.data["scenario_id"] for event in progress} == {"s0", "s1"}
    assert all(event.data["live_frame_path"].startswith("live/") for event in progress)


async def test_no_progress_event_without_frame_and_resize_reason(
    tmp_path: Path,
) -> None:
    bus = EventBus()

    def runner(**_: object):
        time.sleep(0.02)
        return SimpleNamespace(
            scenario_id="s0",
            seed=0,
            status="passed",
            duration_s=0.0,
            sim_time_s=0.0,
            criteria=[],
            diagnosis=None,
            video_path=None,
            trace_path=None,
            error=None,
        )

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        await pool.resize(2, reason="verify fan-out: 2 clusters")
        await pool.submit(
            [{"id": "s0", "index": 0, "seed": 0}],
            model_path="m",
            harness_path="h",
            task={},
        )
    finally:
        await pool.aclose()

    changed = [
        event
        for event in bus.history("run")
        if event.type is EventType.WORKER_POOL_CHANGED
    ]
    assert any(
        event.data["reason"] == "verify fan-out: 2 clusters" for event in changed
    )
    assert not any(
        event.type is EventType.SCENARIO_PROGRESS for event in bus.history("run")
    )


async def test_saturation_events_only_announce_transition_edges(tmp_path: Path) -> None:
    bus = EventBus()
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def runner(*, scenario_id: str, **_: object) -> SimpleNamespace:
        if scenario_id == "s0":
            first_started.set()
            await release.wait()
        return SimpleNamespace(
            scenario_id=scenario_id,
            seed=0,
            status="passed",
            duration_s=0.0,
            sim_time_s=0.0,
            criteria=[],
            diagnosis=None,
            video_path=None,
            trace_path=None,
            error=None,
        )

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    submit = asyncio.create_task(
        pool.submit(
            [{"id": "s0", "index": 0, "seed": 0}, {"id": "s1", "index": 1, "seed": 1}],
            model_path="m",
            harness_path="h",
            task={},
        )
    )
    try:
        await first_started.wait()
        saturated = [
            event
            for event in bus.history("run")
            if event.type is EventType.WORKER_POOL_CHANGED
            and event.data["reason"].startswith("saturated:")
        ]
        assert len(saturated) == 1
        release.set()
        await submit
    finally:
        if not submit.done():
            release.set()
            await submit
        await pool.aclose()

    saturated_events = [
        event
        for event in bus.history("run")
        if event.type is EventType.WORKER_POOL_CHANGED
        and event.data["reason"].startswith("saturated:")
    ]
    assert len(saturated_events) == 1
