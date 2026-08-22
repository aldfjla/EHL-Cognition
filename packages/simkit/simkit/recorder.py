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

import os
from pathlib import Path
from typing import Any

import numpy as np

#: Headless GL backends MuJoCo can render offscreen with. `glfw` needs a
#: display, so a suite running on a server must use one of these.
HEADLESS_BACKENDS = ("egl", "osmesa")
#: 6x8 bitmap glyphs, enough to caption a clip without a font dependency.
GLYPH_W, GLYPH_H = 6, 8
_FONT: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0, 0),
    "0": (0x1E, 0x33, 0x37, 0x3B, 0x33, 0x33, 0x1E, 0),
    "1": (0x0C, 0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x3F, 0),
    "2": (0x1E, 0x33, 0x30, 0x1C, 0x06, 0x33, 0x3F, 0),
    "3": (0x1E, 0x33, 0x30, 0x1C, 0x30, 0x33, 0x1E, 0),
    "4": (0x38, 0x3C, 0x36, 0x33, 0x7F, 0x30, 0x78, 0),
    "5": (0x3F, 0x03, 0x1F, 0x30, 0x30, 0x33, 0x1E, 0),
    "6": (0x1C, 0x06, 0x03, 0x1F, 0x33, 0x33, 0x1E, 0),
    "7": (0x3F, 0x33, 0x30, 0x18, 0x0C, 0x0C, 0x0C, 0),
    "8": (0x1E, 0x33, 0x33, 0x1E, 0x33, 0x33, 0x1E, 0),
    "9": (0x1E, 0x33, 0x33, 0x3E, 0x30, 0x18, 0x0E, 0),
    "A": (0x0C, 0x1E, 0x33, 0x33, 0x3F, 0x33, 0x33, 0),
    "B": (0x3F, 0x66, 0x66, 0x3E, 0x66, 0x66, 0x3F, 0),
    "C": (0x3C, 0x66, 0x03, 0x03, 0x03, 0x66, 0x3C, 0),
    "D": (0x1F, 0x36, 0x66, 0x66, 0x66, 0x36, 0x1F, 0),
    "E": (0x7F, 0x46, 0x16, 0x1E, 0x16, 0x46, 0x7F, 0),
    "F": (0x7F, 0x46, 0x16, 0x1E, 0x16, 0x06, 0x0F, 0),
    "G": (0x3C, 0x66, 0x03, 0x03, 0x73, 0x66, 0x7C, 0),
    "H": (0x33, 0x33, 0x33, 0x3F, 0x33, 0x33, 0x33, 0),
    "I": (0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0),
    "J": (0x78, 0x30, 0x30, 0x30, 0x33, 0x33, 0x1E, 0),
    "K": (0x67, 0x66, 0x36, 0x1E, 0x36, 0x66, 0x67, 0),
    "L": (0x0F, 0x06, 0x06, 0x06, 0x46, 0x66, 0x7F, 0),
    "M": (0x63, 0x77, 0x7F, 0x7F, 0x6B, 0x63, 0x63, 0),
    "N": (0x63, 0x67, 0x6F, 0x7B, 0x73, 0x63, 0x63, 0),
    "O": (0x1C, 0x36, 0x63, 0x63, 0x63, 0x36, 0x1C, 0),
    "P": (0x3F, 0x66, 0x66, 0x3E, 0x06, 0x06, 0x0F, 0),
    "Q": (0x1E, 0x33, 0x33, 0x33, 0x3B, 0x1E, 0x38, 0),
    "R": (0x3F, 0x66, 0x66, 0x3E, 0x36, 0x66, 0x67, 0),
    "S": (0x1E, 0x33, 0x07, 0x0E, 0x38, 0x33, 0x1E, 0),
    "T": (0x3F, 0x2D, 0x0C, 0x0C, 0x0C, 0x0C, 0x1E, 0),
    "U": (0x33, 0x33, 0x33, 0x33, 0x33, 0x33, 0x3F, 0),
    "V": (0x33, 0x33, 0x33, 0x33, 0x33, 0x1E, 0x0C, 0),
    "W": (0x63, 0x63, 0x6B, 0x7F, 0x7F, 0x77, 0x63, 0),
    "X": (0x63, 0x63, 0x36, 0x1C, 0x36, 0x63, 0x63, 0),
    "Y": (0x33, 0x33, 0x33, 0x1E, 0x0C, 0x0C, 0x1E, 0),
    "Z": (0x7F, 0x63, 0x31, 0x18, 0x4C, 0x66, 0x7F, 0),
    ".": (0, 0, 0, 0, 0, 0x0C, 0x0C, 0),
    ",": (0, 0, 0, 0, 0, 0x0C, 0x0C, 0x06),
    ":": (0, 0x0C, 0x0C, 0, 0, 0x0C, 0x0C, 0),
    "-": (0, 0, 0, 0x3F, 0, 0, 0, 0),
    "_": (0, 0, 0, 0, 0, 0, 0, 0x3F),
    "=": (0, 0, 0x3F, 0, 0x3F, 0, 0, 0),
    "+": (0, 0x0C, 0x0C, 0x3F, 0x0C, 0x0C, 0, 0),
    "/": (0x60, 0x30, 0x18, 0x0C, 0x06, 0x03, 0x01, 0),
    "*": (0, 0x36, 0x1C, 0x7F, 0x1C, 0x36, 0, 0),
    "%": (0x43, 0x33, 0x18, 0x0C, 0x06, 0x63, 0x61, 0),
    "(": (0x18, 0x0C, 0x06, 0x06, 0x06, 0x0C, 0x18, 0),
    ")": (0x06, 0x0C, 0x18, 0x18, 0x18, 0x0C, 0x06, 0),
    "!": (0x0C, 0x0C, 0x0C, 0x0C, 0x0C, 0, 0x0C, 0),
    "?": (0x1E, 0x33, 0x30, 0x18, 0x0C, 0, 0x0C, 0),
    "#": (0x36, 0x36, 0x7F, 0x36, 0x7F, 0x36, 0x36, 0),
    "·": (0, 0, 0, 0x0C, 0x0C, 0, 0, 0),
}


class RecorderError(RuntimeError):
    """Offscreen rendering is unavailable or the encode failed."""


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
        self.frames: list[np.ndarray] = []
        self._renderer: Any = None
        self._caption: str = ""

    # -- rendering ---------------------------------------------------------- #

    def _ensure_renderer(self, scene: Any) -> Any:
        """Create the offscreen renderer on first use.

        Deferred because constructing it costs a GL context: a headless suite
        run that records nothing should never touch the GPU.
        """
        if self._renderer is not None:
            return self._renderer
        backend = require_headless_gl()
        import mujoco

        try:
            self._renderer = mujoco.Renderer(
                scene.model, height=self.height, width=self.width
            )
        except Exception as exc:
            raise RecorderError(
                f"could not create an offscreen renderer on MUJOCO_GL={backend}: {exc}"
            ) from exc
        return self._renderer

    def capture(self, scene: Any) -> None:
        """Render the current sim state as one frame."""
        renderer = self._ensure_renderer(scene)
        camera = self.camera if self.camera is not None else -1
        renderer.update_scene(scene.data, camera)
        frame = np.asarray(renderer.render(), dtype=np.uint8).copy()
        if self._caption:
            _draw_text(frame, self._caption)
        self.frames.append(frame)

    def overlay(self, text: str) -> None:
        """Burn a caption into subsequent frames (seed, elapsed time, verdict).

        Worth doing: an unlabelled clip of a robot arm is much harder to read
        than one that says "seed 4471 · friction 0.42 · FAILED at t=2.4s".
        """
        self._caption = str(text or "")

    def save(self, out_path: str) -> str:
        """Encode buffered frames to mp4. Returns the path written."""
        if not self.frames:
            raise RecorderError("no frames captured; nothing to encode")
        import imageio.v2 as imageio

        # imageio's ffmpeg plugin needs a binary; the bundled static one is the
        # only ffmpeg guaranteed to exist on the target machine.
        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe())
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            imageio.mimwrite(
                str(path),
                self.frames,
                format="FFMPEG",
                fps=self.fps,
                codec="libx264",
                quality=6,
                # Frame sizes are ours, not multiples of 16.
                macro_block_size=1,
            )
        except Exception as exc:
            raise RecorderError(f"could not encode {path}: {exc}") from exc
        return str(path)

    def close(self) -> None:
        """Release the renderer and its GL context."""
        renderer, self._renderer = self._renderer, None
        self.frames = []
        if renderer is None:
            return
        # Leaking contexts across a suite run exhausts GPU memory.
        closer = getattr(renderer, "close", None)
        if callable(closer):
            closer()


def require_headless_gl() -> str:
    """Return the configured headless GL backend, or fail loudly."""
    backend = (os.environ.get("MUJOCO_GL") or "").strip().lower()
    if backend in HEADLESS_BACKENDS:
        return backend
    if not backend:
        # EGL is present wherever there is a GPU driver, and is the documented
        # default for this project; choose it rather than failing.
        os.environ["MUJOCO_GL"] = "egl"
        return "egl"
    raise RecorderError(
        f"MUJOCO_GL={backend!r} cannot render offscreen; "
        f"set MUJOCO_GL to one of {', '.join(HEADLESS_BACKENDS)}"
    )


def ffmpeg_exe() -> str:
    """Path to the ffmpeg bundled with imageio-ffmpeg (never a system one)."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise RecorderError(
            "imageio-ffmpeg is required to write video; there is no system ffmpeg"
        ) from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def _draw_text(frame: np.ndarray, text: str, margin: int = 6) -> None:
    """Burn ``text`` into the top-left of ``frame`` in place."""
    scale = 2 if frame.shape[1] >= 480 else 1
    box_h = GLYPH_H * scale + 2 * margin
    box_w = min(frame.shape[1], len(text) * GLYPH_W * scale + 2 * margin)
    band = frame[:box_h, :box_w]
    band[:] = (band * 0.25).astype(np.uint8)
    x = margin
    for char in text.upper():
        glyph = _FONT.get(char)
        if glyph is None:
            glyph = _FONT["?"]
        for row, bits in enumerate(glyph):
            for col in range(GLYPH_W):
                if not bits >> col & 1:
                    continue
                y0 = margin + row * scale
                x0 = x + col * scale
                if x0 + scale > frame.shape[1] or y0 + scale > frame.shape[0]:
                    continue
                frame[y0 : y0 + scale, x0 : x0 + scale] = 255
        x += GLYPH_W * scale
        if x + GLYPH_W * scale > box_w:
            break


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
    from simkit import runner

    result = runner.run_scenario(
        scenario_id=Path(out_path).stem,
        model_path=model_path,
        harness_path=harness_path,
        params=params or {},
        seed=int(seed),
        task=task or {},
        record=str(out_path),
    )
    if result.video_path:
        return result.video_path
    raise RecorderError(result.error or "recording produced no video")
