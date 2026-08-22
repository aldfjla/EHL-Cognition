"""A live process-backed worker pool shared by every suite stage.

Responsibility
--------------
Keep one concurrency budget alive for a run while initial suites, cluster
verification, and final verification submit work. Scenarios acquire stable
slots, so ``scenario.started`` describes the worker actually doing the work
instead of a batch that merely entered the queue.

Inputs: scenario mappings or :class:`~orchestrator.schemas.Scenario` objects,
simulation paths, task configuration, and an optional runner seam for tests.
Outputs: deterministic per-scenario results, worker lifecycle events, and
artifact notifications. Physics execution remains in worker processes; live
frames are only a filesystem side channel.

The current simkit runner does not accept live-frame arguments, so no frames or
progress events are produced by it today. The watcher is implemented against
the documented paths and becomes active when simkit's renderer lands.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.bus import EventBus
from orchestrator.schemas import EventType

log = logging.getLogger(__name__)

Runner = Callable[..., Any]
ResultCallback = Callable[[Any], Awaitable[None] | None]
StartedCallback = Callable[[str, str, int], Awaitable[None] | None]
PARENT_WATCHDOG_GRACE_S = 10.0
DEFAULT_SCENARIO_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class _Spec:
    index: int
    scenario_id: str
    seed: int
    params: dict[str, Any]


def _error_result(spec: _Spec, message: str, *, error_kind: str = "infra") -> Any:
    """Create the same result shape as simkit for infrastructure failures."""
    from simkit.runner import EpisodeResult

    return EpisodeResult(
        scenario_id=spec.scenario_id,
        seed=spec.seed,
        status="error",
        error=message,
        error_kind=error_kind,
    )


def _run_simkit(kwargs: dict[str, Any]) -> Any:
    """Process entry point; lazy import keeps the orchestrator lightweight."""
    from simkit.runner import run_scenario

    return run_scenario(**kwargs)


class SuitePool:
    """Run scenarios through one resizeable, shared process budget."""

    def __init__(
        self,
        *,
        run_id: str,
        bus: EventBus,
        workers: int,
        artifacts_dir: Path,
        runner: Runner | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self.run_id = run_id
        self.bus = bus
        self.artifacts_dir = Path(artifacts_dir)
        self._runner = runner
        self._workers = workers
        self._available: deque[str] = deque(f"w{i}" for i in range(workers))
        self._busy: dict[str, str] = {}
        self._slot_lock = asyncio.Lock()
        self._batch_number = 0
        self._waiters: dict[int, deque[tuple[str, asyncio.Future[str]]]] = {}
        self._batch_order: deque[int] = deque()
        self._queued = 0
        self._executor = ProcessPoolExecutor(max_workers=workers)
        self._executor_shutdowns: set[asyncio.Task[None]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._saturated = False
        self._progress_interval = float(
            os.environ.get("SCENARIO_PROGRESS_INTERVAL_S", "0.5")
        )

    async def submit(
        self,
        specs: Iterable[Any],
        *,
        model_path: str,
        harness_path: str,
        task: dict[str, Any],
        record: str = "failures",
        record_dir: str | Path | None = None,
        repo_dir: str | Path | None = None,
        on_result: ResultCallback | None = None,
        on_started: StartedCallback | None = None,
        reason: str | None = None,
        max_wall_s: float | None = None,
    ) -> list[Any]:
        """Submit scenarios independently and return results in index order.

        Tasks are created one scenario at a time against the pool's shared
        semaphore. This intentionally avoids a batch barrier: a later
        verification submission can use a slot as soon as any earlier scenario
        completes.
        """
        if self._closed:
            raise RuntimeError("suite pool is closed")
        policy = str(record or "none").lower()
        if policy not in {"none", "failures", "all"}:
            raise ValueError("record must be one of ('none', 'failures', 'all')")
        normalized = [_normalize_spec(spec, index) for index, spec in enumerate(specs)]
        if not normalized:
            return []
        if reason:
            await self._emit_pool_changed(reason)
        batch_id = self._batch_number
        self._batch_number += 1
        tasks = [
            self._track(
                self._run_one(
                    spec,
                    batch_id=batch_id,
                    model_path=model_path,
                    harness_path=harness_path,
                    task=task or {},
                    record=policy,
                    record_dir=record_dir,
                    repo_dir=repo_dir,
                    on_result=on_result,
                    on_started=on_started,
                    max_wall_s=max_wall_s,
                )
            )
            for spec in normalized
        ]
        results = await asyncio.gather(*tasks)
        return [
            result
            for _, result in sorted(
                zip((spec.index for spec in normalized), results),
                key=lambda pair: pair[0],
            )
        ]

    async def resize(self, workers: int, *, reason: str) -> None:
        """Change future capacity while preserving in-flight scenarios."""
        if workers < 1:
            raise ValueError("workers must be at least 1")
        async with self._slot_lock:
            old = self._workers
            if workers > old:
                for index in range(old, workers):
                    slot = f"w{index}"
                    if slot not in self._busy and slot not in self._available:
                        self._available.append(slot)
            elif workers < old:
                removable = sorted(
                    (slot for slot in self._available if int(slot[1:]) >= workers),
                    key=lambda slot: int(slot[1:]),
                    reverse=True,
                )
                for slot in removable:
                    self._available.remove(slot)
            self._workers = workers
            if workers > old:
                # Keep executor resizing off-loop; each generation drains its
                # in-flight work while the new high-water executor takes over.
                old_executor = self._executor
                self._executor = ProcessPoolExecutor(max_workers=workers)
                shutdown = asyncio.create_task(
                    asyncio.to_thread(
                        old_executor.shutdown, wait=True, cancel_futures=True
                    )
                )
                self._executor_shutdowns.add(shutdown)
                shutdown.add_done_callback(self._executor_shutdowns.discard)
            self._dispatch_waiters()
            self._set_saturation_locked()
        await self._emit_pool_changed(reason)

    def snapshot(self) -> dict[str, int]:
        """Return the dashboard's current worker and queue counts."""
        return {
            "workers": self._workers,
            "busy": len(self._busy),
            "queued": self._queued,
        }

    async def aclose(self) -> None:
        """Cancel pool tasks and shut down processes without blocking asyncio."""
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        shutdowns = tuple(self._executor_shutdowns)
        shutdowns += (
            asyncio.create_task(
                asyncio.to_thread(
                    self._executor.shutdown, wait=True, cancel_futures=True
                )
            ),
        )
        try:
            await asyncio.shield(asyncio.gather(*shutdowns, return_exceptions=True))
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.gather(*shutdowns, return_exceptions=True))
            raise

    def _track(self, coroutine: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _run_one(
        self,
        spec: _Spec,
        *,
        batch_id: int,
        model_path: str,
        harness_path: str,
        task: dict[str, Any],
        record: str,
        record_dir: str | Path | None,
        repo_dir: str | Path | None,
        on_result: ResultCallback | None,
        on_started: StartedCallback | None,
        max_wall_s: float | None,
    ) -> Any:
        retries = 0
        retry_limit = max(0, int(os.environ.get("SCENARIO_INFRA_RETRIES", "2")))
        while True:
            slot = await self._acquire(spec.scenario_id, batch_id)
            watcher: asyncio.Task[None] | None = None
            try:
                await self.bus.emit(
                    self.run_id,
                    EventType.SCENARIO_STARTED,
                    {
                        "scenario_id": spec.scenario_id,
                        "worker_id": slot,
                        "attempt": retries + 1,
                    },
                )
                if on_started is not None:
                    try:
                        callback_result = on_started(
                            spec.scenario_id, slot, retries + 1
                        )
                        if inspect.isawaitable(callback_result):
                            await callback_result
                    except Exception as exc:  # noqa: BLE001 - callback is best effort
                        log.warning(
                            "started callback failed for %s: %s",
                            spec.scenario_id,
                            exc,
                        )
                watcher = asyncio.create_task(self._watch_progress(spec.scenario_id))
                result = await self._execute(
                    spec,
                    model_path=model_path,
                    harness_path=harness_path,
                    task=task,
                    record=record == "all",
                    record_dir=record_dir,
                    repo_dir=repo_dir,
                    max_wall_s=max_wall_s,
                )
            finally:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                await self._release(slot, spec.scenario_id)
            try:
                result.worker_id = slot
            except AttributeError:
                pass

            error_kind = getattr(result, "error_kind", None)
            is_infra = getattr(result, "status", None) == "error" and (
                error_kind == "infra"
                or (
                    error_kind is None
                    and str(getattr(result, "error", "")).startswith("worker died")
                )
            )
            if not is_infra or retries >= retry_limit:
                break
            retries += 1

        try:
            result.retries = retries
            result.retry_reason = "infra" if retries else None
        except AttributeError:
            pass

        if record == "failures" and getattr(result, "status", None) == "failed":
            replay = await self._execute_with_slot(
                spec,
                model_path=model_path,
                harness_path=harness_path,
                task=task,
                record=True,
                record_dir=record_dir,
                repo_dir=repo_dir,
                max_wall_s=max_wall_s,
            )
            video_path = getattr(replay, "video_path", None)
            if video_path:
                result.video_path = video_path
                # Replay is evidence, not another scenario attempt; the
                # pipeline emits one scenario.finished for the original run.
                await self.bus.emit(
                    self.run_id,
                    EventType.ARTIFACT_CREATED,
                    {
                        "kind": "video",
                        "path": _relative_artifact(video_path, self.artifacts_dir),
                        "scenario_id": spec.scenario_id,
                        "run_id": self.run_id,
                    },
                )
        if on_result is not None:
            try:
                callback_result = on_result(result)
                if inspect.isawaitable(callback_result):
                    await callback_result
            except Exception as exc:  # noqa: BLE001 - callback is best effort
                log.warning(
                    "result callback failed for %s: %s",
                    spec.scenario_id,
                    exc,
                )
        return result

    async def _execute_with_slot(
        self,
        spec: _Spec,
        *,
        model_path: str,
        harness_path: str,
        task: dict[str, Any],
        record: bool,
        record_dir: str | Path | None,
        repo_dir: str | Path | None,
        max_wall_s: float | None,
    ) -> Any:
        slot = await self._acquire(spec.scenario_id, -1)
        try:
            return await self._execute(
                spec,
                model_path=model_path,
                harness_path=harness_path,
                task=task,
                record=record,
                record_dir=record_dir,
                repo_dir=repo_dir,
                max_wall_s=max_wall_s,
            )
        finally:
            await self._release(slot, spec.scenario_id)

    async def _execute(
        self,
        spec: _Spec,
        *,
        model_path: str,
        harness_path: str,
        task: dict[str, Any],
        record: bool,
        record_dir: str | Path | None,
        repo_dir: str | Path | None,
        max_wall_s: float | None,
    ) -> Any:
        kwargs = {
            "scenario_id": spec.scenario_id,
            "model_path": model_path,
            "harness_path": harness_path,
            "params": spec.params,
            "seed": spec.seed,
            "task": task,
            "max_wall_s": (
                float(max_wall_s)
                if max_wall_s is not None
                else float(
                    os.environ.get(
                        "SCENARIO_TIMEOUT_S", str(DEFAULT_SCENARIO_TIMEOUT_S)
                    )
                )
            ),
            "record": _record_path(
                self.artifacts_dir, spec.scenario_id, record_dir=record_dir
            )
            if record
            else False,
        }
        live_dir = self.artifacts_dir / "live"
        kwargs.update(
            {
                "live_frame_path": str(live_dir / f"{spec.scenario_id}.jpg"),
                "progress_path": str(live_dir / f"{spec.scenario_id}.progress.json"),
            }
        )
        if repo_dir is not None:
            kwargs["repo_dir"] = str(repo_dir)
        try:
            if self._runner is None:
                from simkit.runner import run_scenario

                call_kwargs = _supported_kwargs(run_scenario, kwargs)
                return await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        self._executor, _run_simkit, call_kwargs
                    ),
                    timeout=call_kwargs["max_wall_s"] + PARENT_WATCHDOG_GRACE_S,
                )
            return await asyncio.wait_for(
                self._invoke_runner(kwargs),
                timeout=kwargs["max_wall_s"] + PARENT_WATCHDOG_GRACE_S,
            )
        except asyncio.TimeoutError:
            if self._runner is None:
                self._replace_executor()
            return _error_result(
                spec,
                "parent watchdog expired; worker executor replaced",
                error_kind="timeout",
            )
        except Exception as exc:  # noqa: BLE001 - infrastructure stays a result
            if self._runner is None:
                self._replace_executor()
            return _error_result(
                spec,
                f"worker died: {type(exc).__name__}: {exc}",
                error_kind="infra",
            )

    def _replace_executor(self) -> None:
        """Move future jobs to a fresh executor after a wedged worker."""
        old_executor = self._executor
        self._executor = ProcessPoolExecutor(max_workers=self._workers)
        shutdown = asyncio.create_task(
            asyncio.to_thread(old_executor.shutdown, wait=True, cancel_futures=True)
        )
        self._executor_shutdowns.add(shutdown)
        shutdown.add_done_callback(self._executor_shutdowns.discard)

    async def _invoke_runner(self, kwargs: dict[str, Any]) -> Any:
        """Run an injected seam without putting synchronous work on the loop."""
        call_kwargs = _supported_kwargs(self._runner, kwargs)
        if inspect.iscoroutinefunction(self._runner):
            return await self._runner(**call_kwargs)
        return await asyncio.to_thread(self._runner, **call_kwargs)

    async def _acquire(self, scenario_id: str, batch_id: int) -> str:
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        async with self._slot_lock:
            self._waiters.setdefault(batch_id, deque()).append((scenario_id, waiter))
            if batch_id not in self._batch_order:
                self._batch_order.append(batch_id)
            self._queued += 1
            self._dispatch_waiters()
            saturation_reason = self._set_saturation_locked()
        if saturation_reason:
            await self._emit_pool_changed(saturation_reason)
        try:
            return await waiter
        except asyncio.CancelledError:
            async with self._slot_lock:
                waiters = self._waiters.get(batch_id)
                if waiters is not None:
                    remaining = deque(
                        (sid, future) for sid, future in waiters if future is not waiter
                    )
                    if len(remaining) != len(waiters):
                        self._queued -= 1
                    self._waiters[batch_id] = remaining
                    self._cleanup_batch(batch_id)
                    self._dispatch_waiters()
            raise

    async def _release(self, slot: str, scenario_id: str) -> None:
        async with self._slot_lock:
            self._busy.pop(slot, None)
            active = int(slot[1:]) < self._workers
            if active:
                self._available.append(slot)
            self._dispatch_waiters()
            saturation_reason = self._set_saturation_locked()
        if saturation_reason:
            await self._emit_pool_changed(saturation_reason)

    def _dispatch_waiters(self) -> None:
        """Assign available slots round-robin so batches cannot starve each other."""
        while self._available:
            while self._batch_order:
                batch_id = self._batch_order.popleft()
                waiters = self._waiters.get(batch_id)
                if waiters:
                    break
                self._waiters.pop(batch_id, None)
            else:
                return
            scenario_id, waiter = waiters.popleft()
            if waiters:
                self._batch_order.append(batch_id)
            else:
                self._waiters.pop(batch_id, None)
            if waiter.cancelled():
                self._queued -= 1
                continue
            slot = self._available.popleft()
            self._busy[slot] = scenario_id
            self._queued -= 1
            waiter.set_result(slot)

    def _set_saturation_locked(self) -> str | None:
        saturated = self._queued > 0 and len(self._busy) >= self._workers
        if saturated == self._saturated:
            return None
        self._saturated = saturated
        if saturated:
            return f"saturated: {self._queued} queued"
        return f"capacity free: {max(0, self._workers - len(self._busy))} idle workers"

    def _cleanup_batch(self, batch_id: int) -> None:
        if not self._waiters.get(batch_id):
            self._waiters.pop(batch_id, None)
            try:
                self._batch_order.remove(batch_id)
            except ValueError:
                pass

    async def _emit_pool_changed(self, reason: str) -> None:
        snapshot = self.snapshot()
        await self.bus.emit_throttled(
            self.run_id,
            EventType.WORKER_POOL_CHANGED,
            {**snapshot, "reason": reason},
            key="worker.pool_changed",
            min_interval_s=1.0,
        )

    async def _watch_progress(self, scenario_id: str) -> None:
        """Announce changed JPEGs without ever carrying frame bytes in events."""
        frame = self.artifacts_dir / "live" / f"{scenario_id}.jpg"
        sidecar = self.artifacts_dir / "live" / f"{scenario_id}.progress.json"
        last_mtime: int | None = None
        interval = max(0.001, self._progress_interval / 2)
        while True:
            try:
                mtime = frame.stat().st_mtime_ns
            except FileNotFoundError:
                mtime = None
            if mtime is not None and mtime != last_mtime:
                last_mtime = mtime
                progress, sim_time = _read_progress(sidecar)
                await self.bus.emit_throttled(
                    self.run_id,
                    EventType.SCENARIO_PROGRESS,
                    {
                        "scenario_id": scenario_id,
                        "progress": progress,
                        "sim_time_s": sim_time,
                        "live_frame_path": _relative_artifact(
                            frame, self.artifacts_dir
                        ),
                    },
                    key=f"scenario.progress:{scenario_id}",
                    min_interval_s=self._progress_interval,
                )
            await asyncio.sleep(interval)


def _normalize_spec(spec: Any, index: int) -> _Spec:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(spec, dict):
            return spec.get(name, default)
        return getattr(spec, name, default)

    return _Spec(
        index=int(value("index", index)),
        scenario_id=str(value("id", None) or value("scenario_id", None) or f"s{index}"),
        seed=int(value("seed", 0) or 0),
        params=dict(value("params", None) or {}),
    )


def _supported_kwargs(runner: Runner, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        return kwargs
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return kwargs
    return {
        name: value for name, value in kwargs.items() if name in signature.parameters
    }


def _record_path(
    artifacts_dir: Path, scenario_id: str, *, record_dir: str | Path | None = None
) -> str:
    directory = Path(record_dir) if record_dir is not None else artifacts_dir
    return str(directory / f"{scenario_id}.mp4")


def _relative_artifact(path: str | Path, artifacts_dir: Path) -> str:
    try:
        return Path(path).resolve().relative_to(artifacts_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_progress(path: Path) -> tuple[float | None, float | None]:
    try:
        payload = json.loads(path.read_text())
        return payload.get("progress"), payload.get("sim_time_s")
    except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
        return None, None


__all__ = ["SuitePool"]
