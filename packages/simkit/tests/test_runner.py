"""The oracle's central promise: (model, harness, seed) -> identical result."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np
from simkit import runner


def _run(toy_arm: Path, harness: Path, task: dict, seed: int = 1234, **kw):
    return runner.run_scenario(
        scenario_id="t",
        model_path=str(toy_arm),
        harness_path=str(harness),
        params={"object_mass_kg": 0.4, "friction": 0.8},
        seed=seed,
        task=task,
        **kw,
    )


def test_same_seed_gives_identical_result(toy_arm, sweep_harness, task) -> None:
    first = _run(toy_arm, sweep_harness, task)
    second = _run(toy_arm, sweep_harness, task)

    assert first.status == second.status
    assert first.criteria == second.criteria
    assert first.diagnosis == second.diagnosis
    assert first.sim_time_s == second.sim_time_s
    for key, values in first.trace.items():
        if isinstance(values, np.ndarray):
            assert np.array_equal(values, second.trace[key]), key
        else:
            assert values == second.trace[key], key

    # Everything except the wall clock, which is measured, not simulated.
    ignore = {"trace", "duration_s"}
    left = {k: v for k, v in asdict(first).items() if k not in ignore}
    right = {k: v for k, v in asdict(second).items() if k not in ignore}
    assert left == right


def test_different_seeds_change_the_episode(toy_arm, sweep_harness, task) -> None:
    first = _run(toy_arm, sweep_harness, task, seed=1)
    second = _run(toy_arm, sweep_harness, task, seed=2)
    assert not np.array_equal(first.trace["object_pos"], second.trace["object_pos"]), (
        "scenario seed must perturb the world"
    )


def test_trace_is_populated_and_finite(toy_arm, sweep_harness, task) -> None:
    result = _run(toy_arm, sweep_harness, task)
    assert result.status in {"passed", "failed"}
    assert result.trace["joint_names"], "joint names are needed for diagnoses"
    for key in ("t", "qpos", "qvel", "object_pos", "contact_force"):
        values = result.trace[key]
        assert values.shape[0] > 1, key
        assert np.all(np.isfinite(values)), key
    assert result.sim_time_s > 0


def test_watchdog_reports_error_not_failure(toy_arm, task, tmp_path) -> None:
    hang = tmp_path / "hang_harness.py"
    hang.write_text(
        "import time\n"
        "def run_episode(model, data, params):\n"
        "    while True:\n"
        "        time.sleep(0.01)\n"
    )
    result = _run(toy_arm, hang, task, max_wall_s=1.0)
    # Our timeout is never the customer's failure.
    assert result.status == "error"
    assert "watchdog" in (result.error or "")
    assert result.criteria == []


def test_missing_harness_is_an_error(toy_arm, task, tmp_path) -> None:
    result = _run(toy_arm, tmp_path / "nope.py", task)
    assert result.status == "error"
    assert "not found" in (result.error or "")


def test_harness_without_run_episode_is_an_error(toy_arm, task, tmp_path) -> None:
    bad = tmp_path / "bad_harness.py"
    bad.write_text("VALUE = 1\n")
    result = _run(toy_arm, bad, task)
    assert result.status == "error"
    assert "run_episode" in (result.error or "")


def test_harness_crash_is_reported_as_error(toy_arm, task, tmp_path) -> None:
    boom = tmp_path / "boom_harness.py"
    boom.write_text(
        "def run_episode(model, data, params):\n    raise RuntimeError('boom')\n"
    )
    result = _run(toy_arm, boom, task)
    assert result.status == "error"
    assert "boom" in (result.error or "")


def test_sim_time_limit_ends_the_episode(toy_arm, task, tmp_path) -> None:
    forever = tmp_path / "forever_harness.py"
    forever.write_text(
        "def run_episode(model, data, params):\n"
        "    while True:\n"
        "        params['step']()\n"
    )
    limit = float(task["success"][2]["limit_s"])
    result = _run(toy_arm, forever, task)
    assert result.status in {"passed", "failed"}
    assert result.sim_time_s <= limit + 0.05


def test_load_harness_returns_the_callable(sweep_harness) -> None:
    harness = runner.load_harness(str(sweep_harness))
    assert callable(harness)
