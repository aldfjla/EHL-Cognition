"""Run N scenarios in parallel and aggregate into a score table.

Responsibility
--------------
The full test matrix. Executes every scenario, streams progress so the
dashboard's grid fills in live, and produces the pass/fail table that gates the
run.

Inputs:  the scenario list, a model path, a harness path, a parallelism budget.
Outputs: per-scenario :class:`~simkit.runner.EpisodeResult` objects and
         aggregate stats.

Parallelism
-----------
Process-based, not threads: MuJoCo releases the GIL unevenly and the customer's
control code is arbitrary Python. Processes also contain a crash — a segfault in
one scenario must not take the suite with it. Default width is
``min(cpu_count(), len(scenarios))``.

Determinism under parallelism
-----------------------------
Results are collected out of order and **must** be re-sorted by scenario index
before returning. A suite whose output order depends on scheduling produces
different clusters run-to-run, which would make the whole system look flaky.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from simkit.runner import EpisodeResult, run_scenario

#: Recording policies accepted by ``record``.
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
) -> list[Any]:
    """Execute every scenario. Returns results sorted by scenario index.

    ``on_progress`` is called as each scenario finishes, with enough detail for
    the dashboard to fill one cell. It must be cheap and must not raise — a
    progress callback that throws will not be allowed to fail the suite.
    """
    scenarios = list(scenarios or [])
    if not scenarios:
        return []
    policy = str(record or "none").lower()
    if policy not in RECORD_POLICIES:
        raise ValueError(f"record must be one of {RECORD_POLICIES}, got {record!r}")

    width = int(parallel or 0) or min(os.cpu_count() or 1, len(scenarios))
    width = max(1, min(width, len(scenarios)))
    total = len(scenarios)
    results: dict[int, EpisodeResult] = {}

    with ProcessPoolExecutor(max_workers=width) as pool:
        futures = {
            pool.submit(
                run_scenario,
                scenario_id=str(scenario.get("id") or f"s{index}"),
                model_path=model_path,
                harness_path=harness_path,
                params=dict(scenario.get("params") or {}),
                seed=int(scenario.get("seed") or 0),
                task=task or {},
                record=_video_path(scenario, index) if policy == "all" else False,
            ): (index, scenario)
            for index, scenario in enumerate(scenarios)
        }
        for done in as_completed(futures):
            index, scenario = futures[done]
            try:
                result = done.result()
            except Exception as exc:  # noqa: BLE001 - a dead worker is ours
                # A worker that died (segfault, OOM) is our failure, not the
                # customer's: report it as an error, keep the suite alive.
                result = EpisodeResult(
                    scenario_id=str(scenario.get("id") or f"s{index}"),
                    seed=int(scenario.get("seed") or 0),
                    status="error",
                    error=f"worker died: {type(exc).__name__}: {exc}",
                )
            results[index] = result
            _notify(on_progress, result, index, total)

    ordered = [results[i] for i in sorted(results)]

    if policy == "failures":
        # Re-running a failing seed is free correctness-wise — the seed makes it
        # the same episode — and keeps recording off the happy path.
        for index, result in enumerate(ordered):
            if result.status != "failed":
                continue
            scenario = scenarios[index]
            replay = run_scenario(
                scenario_id=result.scenario_id,
                model_path=model_path,
                harness_path=harness_path,
                params=dict(scenario.get("params") or {}),
                seed=result.seed,
                task=task or {},
                record=_video_path(scenario, index),
            )
            if replay.video_path:
                result.video_path = replay.video_path
                _notify(on_progress, result, index, total)

    return ordered


def summarize(results: list[Any]) -> dict[str, Any]:
    """Aggregate into ``{total, passed, failed, errored, pass_rate}``.

    ``errored`` is counted separately from ``failed`` everywhere it surfaces:
    conflating "our simulator broke" with "their robot broke" is the fastest way
    to lose a user's trust in a CI system.
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
        # Errored scenarios never ran the robot, so they cannot count against it.
        "pass_rate": round(passed / scored, 4) if scored else 0.0,
    }


def compare(before: list[Any], after: list[Any]) -> dict[str, Any]:
    """Diff two suite runs — the VERIFY gate's core question.

    Returns fixed / still-failing / newly-broken seed lists. The third is the
    one that matters: a fix that trades one failure for another must be caught
    here, not by the customer.
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
        # The gate: a fix must repair something and break nothing.
        "improved": bool(fixed) and not newly_broken,
        "only_in_before": sorted(set(old) - set(new)),
        "only_in_after": sorted(set(new) - set(old)),
    }


def _notify(
    on_progress: Callable[[dict[str, Any]], None] | None,
    result: EpisodeResult,
    index: int,
    total: int,
) -> None:
    """Report one finished scenario; a broken callback never fails the suite."""
    if on_progress is None:
        return
    try:
        on_progress(
            {
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
        )
    except Exception:  # noqa: BLE001 - progress must never fail the suite
        return


def _video_path(scenario: dict[str, Any], index: int) -> str:
    artifacts = Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
    name = str(scenario.get("id") or f"s{index}")
    return str(artifacts / f"{name}.mp4")
