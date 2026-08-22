"""Render simulation frames to mp4 video.

Responsibility
--------------
Produce the visual evidence: a clip of the robot failing, and the matching clip
of the same seed passing after the fix. These two files side by side are the
proof that the system did something real.

Inputs:  a :class:`~simkit.scene.Scene` and the frames captured during a run.
Outputs: an mp4 under ``ARTIFACTS_DIR``, referenced by
         ``Scenario.video_path`` and by the report's incidents.

ffmpeg
------
There is no system ffmpeg on the target machine, by design. ``imageio-ffmpeg``
ships its own static binary; resolve it with
``imageio_ffmpeg.get_ffmpeg_exe()`` and never shell out to a bare ``ffmpeg``.

Cost
----
Offscreen rendering dominates suite wall-clock time. ``policy.record_video`` in
``robotci.yaml`` defaults to ``failures``: run the suite headless, then re-run
only the failing seeds with recording on. Re-running is free correctness-wise
because seeds are deterministic — the recorded episode is the same episode.
"""

from __future__ import annotations

from typing import Any


class Recorder:
    """Captures frames from an offscreen MuJoCo renderer and writes mp4."""

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        camera: str | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.camera = camera
        # TODO(build): lazily create mujoco.Renderer; it needs an EGL/OSMesa
        # context — set MUJOCO_GL=egl in the env and fail loudly if unavailable.

    def capture(self, scene: Any) -> None:
        """Render the current sim state as one frame."""
        raise NotImplementedError
        # TODO(build): renderer.update_scene(data, camera), render, append.

    def overlay(self, text: str) -> None:
        """Burn a caption into subsequent frames (seed, elapsed time, verdict).

        Worth doing: an unlabelled clip of a robot arm is much harder to read
        than one that says "seed 4471 · friction 0.42 · FAILED at t=2.4s".
        """
        raise NotImplementedError
        # TODO(build): draw text into the frame buffer before appending.

    def save(self, out_path: str) -> str:
        """Encode buffered frames to mp4. Returns the path written."""
        raise NotImplementedError
        # TODO(build): imageio.mimwrite with ffmpeg plugin, macro_block_size=1,
        # quality tuned for small files that still show the failure.

    def close(self) -> None:
        """Release the renderer and its GL context."""
        raise NotImplementedError
        # TODO(build): free the renderer; leaking contexts across a suite run
        # will exhaust GPU memory.


def record_scenario(
    *,
    model_path: str,
    harness_path: str,
    params: dict[str, Any],
    seed: int,
    task: dict[str, Any],
    out_path: str,
) -> str:
    """Re-run one seed with recording on. Returns the mp4 path.

    Convenience wrapper used for the ``record_video: failures`` path and for
    producing the "after" clip once a fix lands.
    """
    raise NotImplementedError
    # TODO(build): runner.run_scenario(record=True) with a Recorder attached.
