"""Headless video capture: EGL/OSMesa only, bundled ffmpeg only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from simkit import recorder
from simkit import scene as scene_mod


@pytest.fixture
def egl() -> str:
    """Skip rather than fail where no offscreen GL backend exists."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    try:
        return recorder.require_headless_gl()
    except recorder.RecorderError as exc:  # pragma: no cover - machine dependent
        pytest.skip(str(exc))


def test_require_headless_gl_rejects_a_windowed_backend(monkeypatch) -> None:
    monkeypatch.setenv("MUJOCO_GL", "glfw")
    with pytest.raises(recorder.RecorderError, match="egl|osmesa"):
        recorder.require_headless_gl()
    monkeypatch.setenv("MUJOCO_GL", "osmesa")
    assert recorder.require_headless_gl() == "osmesa"


def test_ffmpeg_is_the_bundled_binary_not_the_system_one() -> None:
    exe = recorder.ffmpeg_exe()
    assert Path(exe).is_file()
    assert "imageio_ffmpeg" in exe, "a system ffmpeg is not guaranteed to exist"
    assert (
        subprocess.run([exe, "-version"], capture_output=True, check=False).returncode
        == 0
    )


def test_save_without_frames_is_an_error(tmp_path) -> None:
    with pytest.raises(recorder.RecorderError, match="no frames"):
        recorder.Recorder().save(str(tmp_path / "empty.mp4"))


def test_capture_and_save_writes_a_playable_mp4(egl, toy_arm, tmp_path) -> None:
    scene = scene_mod.build(
        scene_mod.SceneSpec(robot_model_path=str(toy_arm), task_name="pick_and_place")
    )
    scene_mod.reset(scene, 3)
    rec = recorder.Recorder(width=240, height=180, fps=10)
    rec.overlay("SEED 3 - FRICTION 0.80")
    try:
        for _ in range(5):
            rec.capture(scene)
        frame = rec.frames[0]
        assert frame.shape == (180, 240, 3)
        # The caption band is burned in, so the clip is readable standalone.
        assert np.any(frame[:20, :100] != rec.frames[0][100:120, 100:200])
        out = rec.save(str(tmp_path / "clip.mp4"))
    finally:
        rec.close()
    assert Path(out).stat().st_size > 1000
    assert rec.frames == [], "close() must drop buffered frames"

    import imageio.v2 as imageio

    with imageio.get_reader(out) as reader:
        assert reader.count_frames() >= 4


def test_record_scenario_produces_video_for_one_seed(
    egl, toy_arm, sweep_harness, task, tmp_path
) -> None:
    out = recorder.record_scenario(
        model_path=str(toy_arm),
        harness_path=str(sweep_harness),
        params={"object_mass_kg": 0.4},
        seed=17,
        task=task,
        out_path=str(tmp_path / "seed17.mp4"),
    )
    assert Path(out).stat().st_size > 1000


def test_record_scenario_surfaces_oracle_errors(egl, toy_arm, task, tmp_path) -> None:
    with pytest.raises(recorder.RecorderError, match="not found"):
        recorder.record_scenario(
            model_path=str(toy_arm),
            harness_path=str(tmp_path / "absent.py"),
            params={},
            seed=1,
            task=task,
            out_path=str(tmp_path / "nope.mp4"),
        )
