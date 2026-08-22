"""Live frame and progress side channels are best-effort additions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from simkit import live, runner


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

    assert result.status in {"passed", "failed"}
    assert len(observations) >= 2
    assert observations[0]["kind"] == "scenario_progress"
    assert observations[0]["scenario_id"] == "progress"
    assert observations[0]["seed"] == 11
    assert all(0.0 <= event["progress"] <= 1.0 for event in observations)
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
