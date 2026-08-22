"""Worker slot scheduling, event forwarding, and recovery."""

from __future__ import annotations

import threading
from pathlib import Path

from simkit.pool import Job, WorkerPool


def make_jobs(toy_arm: Path, sweep_harness: Path, task: dict, count: int) -> list[Job]:
    return [
        Job(
            index=index,
            scenario_id=f"pool-{index}",
            seed=100 + index,
            params={"object_mass_kg": 0.4},
            model_path=str(toy_arm),
            harness_path=str(sweep_harness),
            task=task,
        )
        for index in range(count)
    ]


def test_workers_report_stable_distinct_ids(toy_arm, sweep_harness, task) -> None:
    events: list[dict] = []
    with WorkerPool(workers=2, on_event=events.append) as pool:
        batch = pool.submit(make_jobs(toy_arm, sweep_harness, task, 4))
        results = batch.results(timeout=30)

    assert len(results) == 4
    started = [event for event in events if event["kind"] == "scenario_started"]
    assert {event["worker_id"] for event in started} == {"w0", "w1"}
    assert all(result.worker_id in {"w0", "w1"} for result in results)


def test_resize_emits_reason_and_reuses_pool(toy_arm, sweep_harness, task) -> None:
    events: list[dict] = []
    pool = WorkerPool(workers=1, on_event=events.append)
    try:
        pool.start()
        pool.resize(2, reason="verify fan-out: 3 clusters")
        assert pool.state()["workers"] == 2
        assert any(
            event["kind"] == "pool_changed"
            and event["reason"] == "verify fan-out: 3 clusters"
            for event in events
        )
        assert pool.submit(make_jobs(toy_arm, sweep_harness, task, 2)).results(
            timeout=30
        )
    finally:
        pool.shutdown()


def test_submission_can_drain_concurrently(toy_arm, sweep_harness, task) -> None:
    with WorkerPool(workers=2) as pool:
        first = pool.submit(make_jobs(toy_arm, sweep_harness, task, 3))
        second = pool.submit(make_jobs(toy_arm, sweep_harness, task, 2))
        assert len(first.results(timeout=30)) == 3
        assert len(second.results(timeout=30)) == 2


def test_dead_worker_is_replaced_and_pool_remains_usable(
    toy_arm, sweep_harness, task, tmp_path
) -> None:
    crash = tmp_path / "crash.py"
    crash.write_text(
        "import os\ndef run_episode(model, data, params):\n    os._exit(1)\n"
    )
    jobs = make_jobs(toy_arm, sweep_harness, task, 2)
    jobs[0] = Job(
        index=0,
        scenario_id="crash",
        seed=jobs[0].seed,
        params=jobs[0].params,
        model_path=jobs[0].model_path,
        harness_path=str(crash),
        task=jobs[0].task,
    )
    with WorkerPool(workers=2) as pool:
        results = pool.submit(jobs).results(timeout=30)
        assert results[0].status == "error"
        assert "worker died" in (results[0].error or "")
        assert results[1].status in {"passed", "failed"}
        follow_up = pool.submit(make_jobs(toy_arm, sweep_harness, task, 1))
        assert follow_up.results(timeout=30)[0].status in {"passed", "failed"}


def test_batch_cancellation_replaces_running_worker(
    toy_arm, sweep_harness, task, tmp_path
) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text(
        "import time\n"
        "def run_episode(model, data, params):\n"
        "    while True:\n"
        "        time.sleep(0.01)\n"
    )
    started = threading.Event()
    events: list[dict] = []

    def observe(event: dict) -> None:
        events.append(event)
        if event["kind"] == "scenario_started":
            started.set()

    first, second = make_jobs(toy_arm, sweep_harness, task, 2)
    first = Job(
        index=0,
        scenario_id="hang",
        seed=first.seed,
        params=first.params,
        model_path=first.model_path,
        harness_path=str(hang),
        task=first.task,
    )
    with WorkerPool(workers=1, on_event=observe) as pool:
        batch = pool.submit([first, second])
        assert started.wait(10)
        assert batch.cancel() == 2
        assert batch.cancelled_indexes == [0, 1]
        assert batch.results(timeout=10) == []
        follow_up = pool.submit(make_jobs(toy_arm, sweep_harness, task, 1))
        assert follow_up.results(timeout=30)[0].status in {"passed", "failed"}
