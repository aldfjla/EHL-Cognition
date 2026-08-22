"""Run deterministic scenario matrices on the explicit simkit worker pool.

Responsibility
--------------
Turn scenario specifications into jobs, own suite ordering and recording
policy, and aggregate results. The suite is the boundary between a scenario
matrix and the oracle's one-scenario runner.

Parallelism
-----------
Parallelism belongs to the parent-side :class:`~simkit.pool.WorkerPool`;
workers only execute one deterministic scenario at a time. Scheduling state,
callbacks, cancellation, and recovery stay outside the simulation process.

Determinism-under-parallelism
-----------------------------
Keeping scheduling outside the simulation means a run has the same
``(model, harness, seed)`` result regardless of pool width, worker replacement,
or live-feed observers.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from simkit.pool import Job, WorkerPool
from simkit.runner import EpisodeResult

RECORD_POLICIES = ("none", "failures", "all")


def run_suite(
    *,
    scenarios: list[dict[str, Any]],
    model_path: str,
    harness_path: str,
    task: dict[str, Any],
    parallel: int | None = None,
    record: str = "failures",
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    live: bool = False,
    observe_hz: float = 2.0,
    pool: WorkerPool | None = None,
) -> list[EpisodeResult]:
    """Execute scenarios, returning results ordered by their input index.

    Progress is emitted from pool events so caller-owned pools and internally
    owned pools share one notification path.
    """
    scenarios = list(scenarios or [])
    if not scenarios:
        return []
    policy = str(record or "none").lower()
    if policy not in RECORD_POLICIES:
        raise ValueError(f"record must be one of {RECORD_POLICIES}, got {record!r}")

    width = int(parallel or 0) or min(os.cpu_count() or 1, len(scenarios))
    width = max(1, min(width, len(scenarios)))
    owned_pool = pool is None
    event_pool = pool
    if event_pool is None:
        event_pool = WorkerPool(
            workers=width,
            on_event=_event_dispatcher(on_event, on_progress, len(scenarios)),
        )
    remove_listener = None
    if not owned_pool and (on_event is not None or on_progress is not None):
        remove_listener = event_pool.add_event_listener(
            _event_dispatcher(on_event, on_progress, len(scenarios))
        )

    jobs = _jobs(
        scenarios,
        model_path=model_path,
        harness_path=harness_path,
        task=task,
        record_policy=policy,
        live=live,
        observe_hz=observe_hz,
    )
    try:
        batch = event_pool.submit(jobs)
        results_by_index = batch.results_by_index()

        if policy == "failures":
            replay_jobs = [
                _replay_job(job, result)
                for job in jobs
                if (result := results_by_index.get(job.index)) is not None
                if result.status == "failed"
            ]
            if replay_jobs:
                replay = event_pool.submit(
                    replay_jobs, reason="record failing scenarios"
                )
                for index, replay_result in replay.results_by_index().items():
                    if replay_result.video_path:
                        results_by_index[index].video_path = replay_result.video_path
        return _results_by_index(results_by_index)
    finally:
        if remove_listener is not None:
            remove_listener()
        if owned_pool:
            event_pool.shutdown()


def run_seeds(
    *,
    pool: WorkerPool,
    scenarios: list[dict[str, Any]],
    model_path: str,
    harness_path: str,
    task: dict[str, Any],
    record: bool | str = False,
    live: bool = False,
    observe_hz: float = 2.0,
) -> list[EpisodeResult]:
    """Run a targeted scenario list on a caller-owned pool."""
    jobs = [
        Job(
            index=index,
            scenario_id=str(scenario.get("id") or f"s{index}"),
            seed=int(scenario.get("seed") or 0),
            params=dict(scenario.get("params") or {}),
            model_path=model_path,
            harness_path=harness_path,
            task=task or {},
            record=record,
            live=live,
            observe_hz=observe_hz,
        )
        for index, scenario in enumerate(scenarios)
    ]
    return pool.submit(jobs).results()


def summarize(results: list[Any]) -> dict[str, Any]:
    """Aggregate suite outcomes without conflating robot and system failures.

    ``errored`` is counted separately everywhere it surfaces: conflating our
    breakage with the robot's is how you lose a user's trust. The pass rate
    therefore excludes infrastructure errors from its denominator.
    """
    results = list(results or [])
    passed = sum(1 for r in results if getattr(r, "status", "") == "passed")
    failed = sum(1 for r in results if getattr(r, "status", "") == "failed")
    errored = sum(1 for r in results if getattr(r, "status", "") == "error")
    scored = passed + failed
    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "pass_rate": round(passed / scored, 4) if scored else 0.0,
    }


def compare(before: list[Any], after: list[Any]) -> dict[str, Any]:
    """Diff runs into fixed, unchanged, and newly broken seeds.

    ``newly_broken`` is the one that matters: a regression can hide behind a
    better aggregate pass rate, but a seed that was good and is now bad needs
    attention regardless of other improvements.
    """
    old = {int(getattr(r, "seed", 0)): r for r in before or []}
    new = {int(getattr(r, "seed", 0)): r for r in after or []}

    fixed: list[int] = []
    still_failing: list[int] = []
    newly_broken: list[int] = []
    for seed in sorted(set(old) & set(new)):
        was_ok = getattr(old[seed], "status", "") == "passed"
        is_ok = getattr(new[seed], "status", "") == "passed"
        if was_ok and not is_ok:
            newly_broken.append(seed)
        elif not was_ok and is_ok:
            fixed.append(seed)
        elif not was_ok and not is_ok:
            still_failing.append(seed)

    return {
        "fixed": fixed,
        "still_failing": still_failing,
        "newly_broken": newly_broken,
        "before": summarize(list(before or [])),
        "after": summarize(list(after or [])),
        "improved": bool(fixed) and not newly_broken,
        "only_in_before": sorted(set(old) - set(new)),
        "only_in_after": sorted(set(new) - set(old)),
    }


def _jobs(
    scenarios: list[dict[str, Any]],
    *,
    model_path: str,
    harness_path: str,
    task: dict[str, Any],
    record_policy: str,
    live: bool,
    observe_hz: float,
) -> list[Job]:
    return [
        Job(
            index=index,
            scenario_id=str(scenario.get("id") or f"s{index}"),
            seed=int(scenario.get("seed") or 0),
            params=dict(scenario.get("params") or {}),
            model_path=model_path,
            harness_path=harness_path,
            task=task or {},
            record=_video_path(scenario, index) if record_policy == "all" else False,
            live=live,
            observe_hz=observe_hz,
        )
        for index, scenario in enumerate(scenarios)
    ]


def _replay_job(job: Job, result: EpisodeResult) -> Job:
    return Job(
        index=job.index,
        scenario_id=job.scenario_id,
        seed=result.seed,
        params=job.params,
        model_path=job.model_path,
        harness_path=job.harness_path,
        task=job.task,
        record=_video_path({"id": job.scenario_id}, job.index),
        live=False,
    )


def _event_dispatcher(
    on_event: Callable[[dict[str, Any]], None] | None,
    on_progress: Callable[[dict[str, Any]], None] | None,
    total: int,
) -> Callable[[dict[str, Any]], None]:
    def dispatch(event: dict[str, Any]) -> None:
        _notify_event(on_event, event)
        if event.get("kind") != "scenario_finished":
            return
        _notify(
            on_progress,
            event,
            int(event.get("index", 0)),
            total,
        )

    return dispatch


def _notify_event(
    callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:  # noqa: BLE001 - observer must not affect the suite
        return


def _notify(
    callback: Callable[[dict[str, Any]], None] | None,
    result: Any,
    index: int,
    total: int,
) -> None:
    if callback is None:
        return
    try:
        if isinstance(result, EpisodeResult):
            payload = {
                "index": index,
                "total": total,
                "id": result.scenario_id,
                "seed": result.seed,
                "status": result.status,
                "duration_s": result.duration_s,
                "sim_time_s": result.sim_time_s,
                "diagnosis": result.diagnosis,
                "video_path": result.video_path,
                "error": result.error,
            }
        else:
            payload = {
                "index": index,
                "total": total,
                "id": result.get("scenario_id"),
                "seed": result.get("seed"),
                "status": result.get("status"),
                "duration_s": result.get("duration_s"),
                "sim_time_s": result.get("sim_time_s"),
                "diagnosis": result.get("diagnosis"),
                "video_path": result.get("video_path"),
                "error": result.get("error"),
            }
        callback(payload)
    except Exception:  # noqa: BLE001 - observer must not affect the suite
        return


def _results_by_index(results: dict[int, EpisodeResult]) -> list[EpisodeResult]:
    return [results[index] for index in sorted(results)]


def _video_path(scenario: dict[str, Any], index: int) -> str:
    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
    name = str(scenario.get("id") or f"s{index}")
    return str(artifacts / f"{name}.mp4")
