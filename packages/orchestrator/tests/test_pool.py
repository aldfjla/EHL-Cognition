"""Shared live pool concurrency, ordering, and side-channel behaviour."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from orchestrator import pool as pool_mod
from orchestrator.bus import EventBus
from orchestrator.pool import SuitePool
from orchestrator.schemas import EventType


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

    def runner(*, live_frame_path: str, progress_path: str, **_: object):
        runner_started.set()
        Path(live_frame_path).write_bytes(b"one")
        Path(progress_path).write_text(json.dumps({"progress": 0.4, "sim_time_s": 1.2}))
        time.sleep(0.03)
        Path(live_frame_path).write_bytes(b"two")
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


def _result(
    scenario_id: str, seed: int, status: str, **fields: object
) -> SimpleNamespace:
    error_kind = fields.pop("error_kind", None)
    return SimpleNamespace(
        scenario_id=scenario_id,
        seed=seed,
        status=status,
        duration_s=0.0,
        sim_time_s=0.0,
        criteria=[],
        diagnosis=None,
        video_path=None,
        trace_path=None,
        error=None,
        error_kind=error_kind,
        **fields,
    )


async def test_queue_and_started_callback_reflect_real_slots(tmp_path: Path) -> None:
    bus = EventBus()
    observed: list[tuple[int, int]] = []
    in_flight = 0
    max_in_flight = 0

    def runner(*, scenario_id: str, seed: int, **_: object) -> SimpleNamespace:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        time.sleep(0.02)
        in_flight -= 1
        return _result(scenario_id, seed, "passed")

    async def on_started(_scenario_id: str, _worker_id: str, _attempt: int) -> None:
        snapshot = pool.snapshot()
        observed.append((snapshot["busy"], snapshot["queued"]))

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=2,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        task = asyncio.create_task(
            pool.submit(
                specs(6),
                model_path="m",
                harness_path="h",
                task={},
                on_started=on_started,
            )
        )
        await asyncio.sleep(0.005)
        assert pool.snapshot()["busy"] == 2
        assert pool.snapshot()["queued"] >= 1
        await task
    finally:
        await pool.aclose()
    assert max_in_flight == 2
    assert observed
    assert pool.snapshot()["queued"] == 0
    assert pool.snapshot()["busy"] == 0


async def test_infrastructure_retries_are_visible_but_failures_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCENARIO_INFRA_RETRIES", "2")
    bus = EventBus()
    calls = 0

    def runner(*, scenario_id: str, seed: int, **_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return _result(
                scenario_id, seed, "error", error="transient", error_kind="infra"
            )
        return _result(scenario_id, seed, "passed")

    attempts: list[int] = []
    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        result = await pool.submit(
            specs(1),
            model_path="m",
            harness_path="h",
            task={},
            on_started=lambda _id, _worker, attempt: attempts.append(attempt),
        )
    finally:
        await pool.aclose()
    assert result[0].status == "passed"
    assert result[0].retries == 2
    assert result[0].retry_reason == "infra"
    assert attempts == [1, 2, 3]


async def test_failed_result_is_not_retried(tmp_path: Path) -> None:
    bus = EventBus()
    calls = 0

    def runner(*, scenario_id: str, seed: int, **_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return _result(scenario_id, seed, "failed", error_kind="infra")

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        result = await pool.submit(
            specs(1),
            model_path="m",
            harness_path="h",
            task={},
            record="none",
        )
    finally:
        await pool.aclose()
    assert result[0].status == "failed"
    assert calls == 1
    assert result[0].retries == 0


async def test_timeout_is_not_retried_and_slot_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pool_mod, "PARENT_WATCHDOG_GRACE_S", 0.01)
    bus = EventBus()

    def runner(*, scenario_id: str, seed: int, **_: object) -> SimpleNamespace:
        if scenario_id == "s0":
            time.sleep(0.05)
        return _result(scenario_id, seed, "passed")

    pool = SuitePool(
        run_id="run",
        bus=bus,
        workers=1,
        artifacts_dir=tmp_path,
        runner=runner,
    )
    try:
        result = await pool.submit(
            specs(2),
            model_path="m",
            harness_path="h",
            task={},
            max_wall_s=0.005,
        )
    finally:
        await pool.aclose()
    assert result[0].status == "error"
    assert result[0].error_kind == "timeout"
    assert result[0].retries == 0
    assert result[1].status == "passed"
