"""A crash-tolerant process pool for deterministic simulation work.

``ProcessPoolExecutor`` is the wrong primitive here: one segfault poisons the
executor, and it has no stable worker identity, resize operation, or per-job
cancellation. Processes isolate arbitrary customer control code and avoid the
GIL that would make threads a poor fit for CPU-heavy harnesses. They also let
MuJoCo use separate GL contexts; ``spawn`` is used instead of ``fork`` because
forking after EGL/GL state has been touched can hang or corrupt contexts.

Scheduling state lives in the parent, which owns one task queue per worker slot
and dispatches only to known idle slots. Events are drained by one dispatcher
thread, and ``on_event`` callbacks run there while the pool lock is held. They
must therefore be cheap and non-blocking.
"""

from __future__ import annotations

import atexit
import multiprocessing
import os
import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.context import BaseContext
from typing import Any, Self

from simkit.runner import EpisodeResult, run_scenario

DEFAULT_MP_CONTEXT = "spawn"
PARENT_WATCHDOG_GRACE_S = 10.0


@dataclass(frozen=True)
class Job:
    index: int
    scenario_id: str
    seed: int
    params: dict[str, Any]
    model_path: str
    harness_path: str
    task: dict[str, Any]
    record: str | bool = False
    live: bool = False
    observe_hz: float = 2.0
    max_wall_s: float = 60.0
    job_id: str = ""


def _worker_loop(task_queue: Any, event_queue: Any, worker_id: str) -> None:
    """Run jobs from one slot until its sentinel arrives."""
    while True:
        job = task_queue.get()
        if job is None:
            return
        _put_event(
            event_queue,
            {
                "kind": "scenario_started",
                "scenario_id": job.scenario_id,
                "worker_id": worker_id,
                "index": job.index,
                "job_id": job.job_id,
                "seed": job.seed,
            },
        )

        def observe(payload: dict[str, Any], _job: Job = job) -> None:
            event = dict(payload)
            event["job_id"] = _job.job_id
            _put_event(event_queue, event)

        result = run_scenario(
            scenario_id=job.scenario_id,
            model_path=job.model_path,
            harness_path=job.harness_path,
            params=job.params,
            seed=job.seed,
            task=job.task,
            record=job.record,
            max_wall_s=job.max_wall_s,
            live=job.live,
            on_observe=observe,
            observe_hz=job.observe_hz,
            worker_id=worker_id,
        )
        _put_event(
            event_queue,
            {
                "kind": "scenario_finished",
                "scenario_id": job.scenario_id,
                "worker_id": worker_id,
                "index": job.index,
                "job_id": job.job_id,
                "seed": job.seed,
                "result": result,
            },
        )


def _put_event(event_queue: Any, event: dict[str, Any]) -> None:
    try:
        event_queue.put(event)
    except Exception:  # noqa: BLE001 - worker may be shutting down
        return


@dataclass
class Batch:
    """Handle for one submission."""

    _pool: WorkerPool
    jobs: tuple[Job, ...]
    id: str
    _results: dict[int, EpisodeResult] = field(default_factory=dict)
    _cancelled: set[int] = field(default_factory=set)
    _condition: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )

    def results(self, timeout: float | None = None) -> list[EpisodeResult]:
        """Block until all jobs are terminal and return jobs that ran."""
        indexed = self.results_by_index(timeout=timeout)
        return [indexed[index] for index in sorted(indexed)]

    def results_by_index(
        self, timeout: float | None = None
    ) -> dict[int, EpisodeResult]:
        """Block until terminal and return results keyed by scenario index."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while len(self._results) + len(self._cancelled) < len(self.jobs):
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                if remaining == 0:
                    raise TimeoutError(f"batch {self.id} did not finish")
                self._condition.wait(remaining)
            return dict(self._results)

    def done(self) -> bool:
        with self._condition:
            return len(self._results) + len(self._cancelled) == len(self.jobs)

    def cancel(self) -> int:
        return self._pool._cancel_batch(self)

    @property
    def cancelled_indexes(self) -> list[int]:
        with self._condition:
            return sorted(self._cancelled)

    def _record_result(self, job: Job, result: EpisodeResult) -> None:
        with self._condition:
            self._results[job.index] = result
            self._condition.notify_all()

    def _record_cancelled(self, job: Job) -> None:
        with self._condition:
            self._cancelled.add(job.index)
            self._condition.notify_all()


@dataclass
class _Slot:
    worker_id: str
    queue: Any
    process: Any
    job: Job | None = None
    job_started_at: float | None = None
    stopping: bool = False
    retiring: bool = False


class WorkerPool:
    """Parent-scheduled process pool whose slots survive worker crashes."""

    def __init__(
        self,
        *,
        workers: int | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        mp_context: str = DEFAULT_MP_CONTEXT,
    ) -> None:
        configured = workers
        if configured is None:
            value = os.environ.get("SIMKIT_WORKERS")
            configured = int(value) if value else min(os.cpu_count() or 1, 8)
        self._target_workers = max(1, int(configured))
        self._on_event = on_event
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._context: BaseContext = multiprocessing.get_context(mp_context)
        self._event_queue = self._context.Queue()
        self._slots: dict[str, _Slot] = {}
        self._queued: deque[tuple[Batch, Job]] = deque()
        self._jobs: dict[str, tuple[Batch, Job]] = {}
        self._lock = threading.RLock()
        self._started = False
        self._shutting_down = False
        self._dispatcher: threading.Thread | None = None
        self._last_reason = ""
        self._shutdown_hook = self.shutdown
        atexit.register(self._shutdown_hook, wait=False)

    @property
    def workers(self) -> int:
        with self._lock:
            return len(self._slots)

    def start(self) -> Self:
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("worker pool has been shut down")
            if self._started:
                return self
            self._started = True
            for index in range(self._target_workers):
                self._spawn_slot(index)
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                name="simkit-worker-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()
            self._emit_state("pool started")
        return self

    def submit(self, jobs: list[Job], *, reason: str = "") -> Batch:
        if self._shutting_down:
            raise RuntimeError("worker pool has been shut down")
        self.start()
        normalized: list[Job] = []
        batch_id = uuid.uuid4().hex
        with self._lock:
            for job in jobs:
                job_id = job.job_id or f"{batch_id}:{job.index}"
                if job_id in self._jobs:
                    raise ValueError(f"duplicate job_id {job_id!r}")
                normalized.append(
                    Job(
                        index=int(job.index),
                        scenario_id=str(job.scenario_id),
                        seed=int(job.seed),
                        params=dict(job.params),
                        model_path=str(job.model_path),
                        harness_path=str(job.harness_path),
                        task=dict(job.task),
                        record=job.record,
                        live=bool(job.live),
                        observe_hz=float(job.observe_hz),
                        max_wall_s=float(job.max_wall_s),
                        job_id=job_id,
                    )
                )
            batch = Batch(self, tuple(normalized), batch_id)
            for job in normalized:
                self._jobs[job.job_id] = (batch, job)
                self._queued.append((batch, job))
            self._dispatch_locked()
            self._emit_state(reason or "jobs submitted")
        return batch

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            entry = self._jobs.get(job_id)
            if entry is None:
                return False
            batch, job = entry
            for queued_batch, queued_job in list(self._queued):
                if queued_job.job_id != job_id:
                    continue
                self._queued.remove((queued_batch, queued_job))
                self._jobs.pop(job_id, None)
                batch._record_cancelled(job)
                self._emit_cancelled(job, None)
                self._emit_state("job cancelled")
                return True
            slot = self._slot_for(job_id)
            if slot is None:
                return False
            slot.job = None
            self._jobs.pop(job_id, None)
            batch._record_cancelled(job)
            self._emit_cancelled(job, slot.worker_id)
            self._terminate_slot(slot)
            self._respawn_slot(slot.worker_id)
            self._emit_state("job cancelled")
            self._dispatch_locked()
            return True

    def resize(self, workers: int, *, reason: str = "") -> None:
        target = max(1, int(workers))
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("worker pool has been shut down")
            self.start()
            self._target_workers = target
            for slot in self._slots.values():
                if int(slot.worker_id[1:]) < target:
                    slot.retiring = False
            while len(self._slots) < target:
                next_index = next(
                    index for index in range(target) if f"w{index}" not in self._slots
                )
                self._spawn_slot(next_index)
            for worker_id in sorted(
                self._slots, key=lambda value: int(value[1:]), reverse=True
            ):
                if len(self._slots) <= target:
                    break
                slot = self._slots[worker_id]
                if slot.job is None:
                    self._stop_slot(slot)
                else:
                    slot.retiring = True
            if len(self._slots) <= target:
                for slot in self._slots.values():
                    slot.retiring = False
            self._last_reason = reason
            self._emit_state(reason or "pool resized")
            self._dispatch_locked()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workers": len(self._slots),
                "busy": sum(slot.job is not None for slot in self._slots.values()),
                "queued": len(self._queued),
                "reason": self._last_reason,
            }

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            atexit.unregister(self._shutdown_hook)
            if self._shutting_down:
                return
            self._shutting_down = True
            if not self._started:
                return
            slots = list(self._slots.values())
            for slot in slots:
                if not slot.stopping:
                    slot.stopping = True
                    slot.queue.put(None)
            self._emit_state("pool shutdown")
        if wait:
            for slot in slots:
                slot.process.join(2.0)
        for slot in slots:
            if slot.process.is_alive():
                slot.process.terminate()
                slot.process.join(2.0)
            slot.queue.close()
        self._event_queue.close()
        dispatcher = self._dispatcher
        if dispatcher is not None and dispatcher is not threading.current_thread():
            dispatcher.join(2.0)
        with self._lock:
            self._slots.clear()
            self._started = False
            self._dispatcher = None

    def add_event_listener(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Subscribe an additional parent-side event listener."""
        with self._lock:
            self._listeners.append(callback)

        def remove() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return remove

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    def _spawn_slot(self, index: int) -> None:
        worker_id = f"w{index}"
        queue = self._context.Queue()
        process = self._context.Process(
            target=_worker_loop,
            args=(queue, self._event_queue, worker_id),
            name=f"simkit-{worker_id}",
        )
        process.daemon = True
        process.start()
        self._slots[worker_id] = _Slot(worker_id, queue, process)

    def _dispatch_loop(self) -> None:
        while True:
            with self._lock:
                if self._shutting_down:
                    return
                self._drain_events_locked()
                self._poll_watchdogs_locked()
                self._poll_deaths_locked()
                self._dispatch_locked()
            time.sleep(0.02)

    def _drain_events_locked(self) -> None:
        while True:
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                return
            kind = event.get("kind")
            if kind == "scenario_finished":
                self._finish_locked(event)
            elif kind == "scenario_started" or kind == "scenario_progress":
                self._call_event(event)

    def _finish_locked(self, event: dict[str, Any]) -> None:
        job_id = event["job_id"]
        entry = self._jobs.pop(job_id, None)
        if entry is None:
            return
        batch, job = entry
        slot = self._slot_for(job_id)
        if slot is not None:
            slot.job = None
            slot.job_started_at = None
            retiring = slot.retiring
        result = event["result"]
        batch._record_result(job, result)
        public = {key: value for key, value in event.items() if key != "result"}
        public.update(_result_event_fields(result))
        self._call_event(public)
        if slot is not None and retiring:
            if len(self._slots) > self._target_workers:
                self._stop_slot(slot)
                self._emit_state("retired worker after resize")
            else:
                slot.retiring = False

    def _poll_deaths_locked(self) -> None:
        for slot in list(self._slots.values()):
            if slot.process.is_alive():
                continue
            exitcode = slot.process.exitcode
            job = slot.job
            if slot.stopping:
                self._slots.pop(slot.worker_id, None)
                slot.queue.close()
                continue
            if job is not None:
                entry = self._jobs.pop(job.job_id, None)
                if entry is not None:
                    batch, _ = entry
                    result = EpisodeResult(
                        scenario_id=job.scenario_id,
                        seed=job.seed,
                        status="error",
                        error_kind="infra",
                        error=f"worker died: exitcode {exitcode}",
                        worker_id=slot.worker_id,
                    )
                    slot.job = None
                    slot.job_started_at = None
                    batch._record_result(job, result)
                    self._call_event(
                        {
                            "kind": "scenario_finished",
                            "scenario_id": job.scenario_id,
                            "worker_id": slot.worker_id,
                            "index": job.index,
                            "job_id": job.job_id,
                            "seed": job.seed,
                            **_result_event_fields(result),
                        }
                    )
            self._slots.pop(slot.worker_id, None)
            slot.queue.close()
            self._spawn_slot(int(slot.worker_id[1:]))
            self._last_reason = (
                f"worker {slot.worker_id} died (exitcode {exitcode}), respawned"
            )
            self._emit_state(self._last_reason)

    def _poll_watchdogs_locked(self) -> None:
        """Terminate a worker that cannot be interrupted by the in-worker guard."""
        now = time.monotonic()
        for slot in list(self._slots.values()):
            job = slot.job
            started = slot.job_started_at
            if job is None or started is None:
                continue
            if now - started <= float(job.max_wall_s) + PARENT_WATCHDOG_GRACE_S:
                continue
            entry = self._jobs.pop(job.job_id, None)
            if entry is None:
                continue
            batch, _ = entry
            result = EpisodeResult(
                scenario_id=job.scenario_id,
                seed=job.seed,
                status="error",
                error_kind="timeout",
                error=(
                    "parent watchdog expired after "
                    f"{float(job.max_wall_s) + PARENT_WATCHDOG_GRACE_S:.1f}s"
                ),
                worker_id=slot.worker_id,
            )
            self._terminate_slot(slot)
            batch._record_result(job, result)
            self._call_event(
                {
                    "kind": "scenario_finished",
                    "scenario_id": job.scenario_id,
                    "worker_id": slot.worker_id,
                    "index": job.index,
                    "job_id": job.job_id,
                    "seed": job.seed,
                    **_result_event_fields(result),
                }
            )
            self._respawn_slot(slot.worker_id)
            self._last_reason = (
                f"worker {slot.worker_id} timed out, terminated and respawned"
            )
            self._emit_state(self._last_reason)

    def _dispatch_locked(self) -> None:
        idle = [
            slot
            for slot in sorted(self._slots.values(), key=lambda item: item.worker_id)
            if slot.job is None and not slot.stopping and not slot.retiring
        ]
        while idle and self._queued:
            _, job = self._queued.popleft()
            slot = idle.pop(0)
            slot.job = job
            slot.job_started_at = time.monotonic()
            slot.queue.put(job)

    def _slot_for(self, job_id: str) -> _Slot | None:
        return next(
            (
                slot
                for slot in self._slots.values()
                if slot.job and slot.job.job_id == job_id
            ),
            None,
        )

    def _cancel_batch(self, batch: Batch) -> int:
        with self._lock:
            jobs = {job.job_id: job for job in batch.jobs}
            cancelled = 0
            for queued_batch, job in list(self._queued):
                if queued_batch is not batch:
                    continue
                self._queued.remove((queued_batch, job))
                self._jobs.pop(job.job_id, None)
                batch._record_cancelled(job)
                self._emit_cancelled(job, None)
                cancelled += 1
            for job_id, job in jobs.items():
                slot = self._slot_for(job_id)
                if slot is None:
                    continue
                self.cancel_job(job_id)
                cancelled += 1
            self._emit_state("batch cancelled")
            self._dispatch_locked()
            return cancelled

    def _terminate_slot(self, slot: _Slot) -> None:
        slot.stopping = True
        if slot.process.is_alive():
            slot.process.terminate()
            slot.process.join(2.0)
        slot.queue.close()
        self._slots.pop(slot.worker_id, None)

    def _respawn_slot(self, worker_id: str) -> None:
        self._spawn_slot(int(worker_id[1:]))

    def _stop_slot(self, slot: _Slot) -> None:
        slot.stopping = True
        slot.queue.put(None)
        slot.process.join(2.0)
        if slot.process.is_alive():
            slot.process.terminate()
            slot.process.join(2.0)
        slot.queue.close()
        self._slots.pop(slot.worker_id, None)

    def _emit_cancelled(self, job: Job, worker_id: str | None) -> None:
        self._call_event(
            {
                "kind": "scenario_cancelled",
                "scenario_id": job.scenario_id,
                "worker_id": worker_id,
                "index": job.index,
                "job_id": job.job_id,
                "seed": job.seed,
            }
        )

    def _emit_state(self, reason: str) -> None:
        self._last_reason = reason
        self._call_event(
            {
                "kind": "pool_changed",
                "workers": len(self._slots),
                "busy": sum(slot.job is not None for slot in self._slots.values()),
                "queued": len(self._queued),
                "reason": reason,
            }
        )

    def _call_event(self, event: dict[str, Any]) -> None:
        callbacks = ([self._on_event] if self._on_event is not None else []) + list(
            self._listeners
        )
        for callback in callbacks:
            _safe_call(callback, event)


def _safe_call(
    callback: Callable[[dict[str, Any]], None], event: dict[str, Any]
) -> None:
    try:
        callback(event)
    except Exception:  # noqa: BLE001 - observers must not affect simulation
        return


def _result_event_fields(result: EpisodeResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "duration_s": result.duration_s,
        "sim_time_s": result.sim_time_s,
        "diagnosis": result.diagnosis,
        "video_path": result.video_path,
        "error": result.error,
        "error_kind": result.error_kind,
        "retries": result.retries,
        "retry_reason": result.retry_reason,
    }
