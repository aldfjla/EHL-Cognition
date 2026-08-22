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

from collections.abc import Callable
from typing import Any


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
    raise NotImplementedError
    # TODO(build): ProcessPoolExecutor, submit run_scenario per scenario,
    # as_completed -> on_progress, then sort by index; re-run failures with
    # record=True when record == "failures".


def summarize(results: list[Any]) -> dict[str, Any]:
    """Aggregate into ``{total, passed, failed, errored, pass_rate}``.

    ``errored`` is counted separately from ``failed`` everywhere it surfaces:
    conflating "our simulator broke" with "their robot broke" is the fastest way
    to lose a user's trust in a CI system.
    """
    raise NotImplementedError
    # TODO(build): count by status, compute pass_rate over non-errored.


def compare(before: list[Any], after: list[Any]) -> dict[str, Any]:
    """Diff two suite runs — the VERIFY gate's core question.

    Returns fixed / still-failing / newly-broken seed lists. The third is the
    one that matters: a fix that trades one failure for another must be caught
    here, not by the customer.
    """
    raise NotImplementedError
    # TODO(build): match by seed, classify each into the three buckets.
