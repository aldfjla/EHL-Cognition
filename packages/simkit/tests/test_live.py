"""Live frame and progress side channels are best-effort additions."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest
from simkit import live, runner, suite
from simkit import scenarios as scenarios_mod


class _FakeRenderer:
    def __init__(self) -> None:
        self.closed = False
        self.frames = 0

    def update_scene(self, data, camera) -> None:
        del data, camera

    def render(self) -> np.ndarray:
        self.frames += 1
        return np.full((8, 10, 3), self.frames, dtype=np.uint8)

    def close(self) -> None:
        self.closed = True


def test_live_frame_is_atomic_and_removed_on_close(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    writer = live.LiveFrameWriter("run/unsafe scenario", fps=10)
    renderer = _FakeRenderer()
    writer._renderer = renderer
    monkeypatch.setattr(writer, "_ensure_renderer", lambda scene: renderer)
    scene = SimpleNamespace(data=object())

    assert writer.maybe_capture(scene, force=True)
    assert writer.has_frame
    path = live.live_frame_file("run/unsafe scenario")
    assert path == tmp_path / live.live_frame_path("run/unsafe scenario")
    first = path.read_bytes()
    assert first

    assert writer.maybe_capture(scene, force=True)
    assert path.read_bytes() != first
    assert not list(path.parent.glob("*.tmp"))

    writer.close()
    writer.close()
    assert not path.exists()
    assert renderer.closed


def test_live_frame_is_wall_clock_throttled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    writer = live.LiveFrameWriter("cadence", fps=2)
    renderer = _FakeRenderer()
    monkeypatch.setattr(writer, "_ensure_renderer", lambda scene: renderer)
    scene = SimpleNamespace(data=object())
    clock = iter((0.0, 0.1, 0.49, 0.5, 0.6, 1.0))
    monkeypatch.setattr(live.time, "monotonic", lambda: next(clock))

    assert writer.maybe_capture(scene)
    assert not writer.maybe_capture(scene)
    assert not writer.maybe_capture(scene)
    assert writer.maybe_capture(scene)
    assert not writer.maybe_capture(scene)
    assert writer.maybe_capture(scene)
    assert renderer.frames == 3
    writer.close()


def test_rendering_failure_disables_writer_without_raising(monkeypatch) -> None:
    writer = live.LiveFrameWriter("broken", fps=10)

    def explode(scene):
        del scene
        raise RuntimeError("no EGL")

    monkeypatch.setattr(writer, "_ensure_renderer", explode)
    assert writer.maybe_capture(object(), force=True) is False
    assert writer.drops == 1
    assert not writer.enabled
    assert writer.maybe_capture(object(), force=True) is False
    assert writer.drops == 1
    writer.close()


def test_disabled_environment_never_creates_renderer(monkeypatch) -> None:
    monkeypatch.setenv("SIMKIT_LIVE_FRAMES", "off")
    writer = live.LiveFrameWriter("disabled")
    assert not writer.enabled
    assert writer.maybe_capture(object(), force=True) is False
    writer.close()


def test_runner_observations_are_throttled_and_observer_errors_swallowed(
    toy_arm: Path, sweep_harness: Path, task: dict
) -> None:
    observations: list[dict] = []
    started = time.monotonic()

    def observe(payload: dict) -> None:
        observations.append(payload)
        raise RuntimeError("dashboard unavailable")

    result = runner.run_scenario(
        scenario_id="progress",
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        params={"object_mass_kg": 0.4},
        seed=11,
        task=task,
        on_observe=observe,
        observe_hz=2.0,
    )
    elapsed = time.monotonic() - started

    assert result.status in {"passed", "failed"}
    assert len(observations) >= 2
    assert len(observations) <= 2.0 * elapsed + 3.0
    assert observations[0]["kind"] == "scenario_progress"
    assert observations[0]["scenario_id"] == "progress"
    assert observations[0]["seed"] == 11
    assert all(0.0 <= event["progress"] <= 1.0 for event in observations)
    assert [event["progress"] for event in observations] == sorted(
        event["progress"] for event in observations
    )
    assert all(
        set(event)
        == {
            "kind",
            "scenario_id",
            "seed",
            "worker_id",
            "progress",
            "sim_time_s",
            "live_frame_path",
        }
        for event in observations
    )


def test_on_step_harness_reports_progress(
    toy_arm: Path, task: dict, tmp_path: Path
) -> None:
    harness = tmp_path / "on_step_harness.py"
    harness.write_text(
        "import time\n"
        "import mujoco\n\n"
        "def run_episode(model, data, params):\n"
        "    for _ in range(25):\n"
        "        mujoco.mj_step(model, data)\n"
        "        params['on_step']()\n"
        "        time.sleep(0.01)\n"
    )
    observations: list[dict] = []

    result = runner.run_scenario(
        scenario_id="on-step",
        model_path=str(toy_arm),
        harness_path=str(harness),
        params={},
        seed=4,
        task=task,
        on_observe=observations.append,
        observe_hz=10.0,
    )

    assert result.status in {"passed", "failed"}
    assert len(observations) > 2
    assert any(event["sim_time_s"] > 0 for event in observations[1:-1])


def test_renderer_failure_does_not_make_scenario_error(
    toy_arm: Path, sweep_harness: Path, task: dict, monkeypatch
) -> None:
    def explode(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(runner.mujoco, "Renderer", explode)
    result = runner.run_scenario(
        scenario_id="renderer-failure",
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        params={"object_mass_kg": 0.4},
        seed=8,
        task=task,
        live=True,
    )

    assert result.status in {"passed", "failed"}


def test_real_live_frame_is_published_during_run_and_removed_afterwards(
    toy_arm: Path,
    sweep_harness: Path,
    task: dict,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    try:
        model = mujoco.MjModel.from_xml_path(str(toy_arm))
        renderer = mujoco.Renderer(model, height=32, width=32)
        renderer.close()
    except (mujoco.FatalError, OSError, RuntimeError, ValueError) as exc:
        pytest.skip(f"headless renderer unavailable: {exc}")

    frame = live.live_frame_file("real-live")
    observed_paths: list[Path] = []

    def observe(payload: dict) -> None:
        path = payload["live_frame_path"]
        if path is not None:
            absolute = tmp_path / path
            observed_paths.append(absolute)
            assert absolute.exists()

    result = runner.run_scenario(
        scenario_id="real-live",
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        params={"object_mass_kg": 0.4},
        seed=12,
        task=task,
        live=True,
        on_observe=observe,
        observe_hz=20.0,
    )

    assert result.status in {"passed", "failed"}
    assert observed_paths
    assert frame in observed_paths
    assert not frame.exists()


def test_suite_is_deterministic_across_pool_width_and_live(
    toy_arm: Path, sweep_harness: Path, task: dict, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    scenarios = scenarios_mod.generate(
        "determinism", 1234, 2, {"object_mass_kg": (0.2, 0.6)}
    )
    runs = [
        suite.run_suite(
            scenarios=scenarios,
            model_path=str(toy_arm),
            harness_path=str(sweep_harness),
            task=task,
            parallel=parallel,
            record="none",
            live=enabled,
        )
        for parallel in (1, 2)
        for enabled in (False, True)
    ]
    baseline = runs[0]
    for compared in runs[1:]:
        for expected, actual in zip(baseline, compared, strict=True):
            assert actual.status == expected.status
            assert actual.criteria == expected.criteria
            assert actual.diagnosis == expected.diagnosis
            assert actual.sim_time_s == expected.sim_time_s
            for key in ("qpos", "qvel", "object_pos", "contact_force", "t"):
                assert np.array_equal(expected.trace[key], actual.trace[key]), (
                    key,
                    expected.worker_id,
                    actual.worker_id,
                )


def test_progress_sidecar_is_atomic_and_transient(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setenv("SIMKIT_LIVE_FRAMES", "1")
    writer = live.LiveProgressWriter(tmp_path / "live/s0.progress.json", fps=30)

    assert writer.maybe_write(progress=0.25, sim_time_s=1.5, force=True)
    sidecar = tmp_path / "live/s0.progress.json"
    assert sidecar.read_text() == '{"progress":0.25,"sim_time_s":1.5}'
    assert not list(sidecar.parent.glob("*.tmp"))

    writer.close()
    assert not sidecar.exists()
