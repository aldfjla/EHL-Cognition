"""Live simulation frame side channel.

Frames are deliberately kept out of the event stream. A single JPEG is
overwritten in place for each scenario, allowing a browser to fetch the latest
state without retaining a video history or feeding rendering back into the
physics loop. Atomic publication uses a same-directory temporary file and
``os.replace``. Any rendering, encoding, or filesystem failure disables the
feed entirely: it never becomes a scenario error and never perturbs physics.
That is also why visual compilation remains driven by ``record`` alone.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import mujoco

DEFAULT_LIVE_FPS = 4.0
DEFAULT_LIVE_SIZE = (480, 360)


def _artifacts_dir() -> Path:
    return Path(os.environ.get("ARTIFACTS_DIR", "artifacts")).expanduser().resolve()


def _safe_scenario_id(scenario_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(scenario_id))
    return safe.strip("._") or "scenario"


def live_frame_path(scenario_id: str) -> str:
    """Return the path of a scenario's frame relative to the artifacts root."""
    return f"live/{_safe_scenario_id(scenario_id)}.jpg"


def live_frame_file(scenario_id: str) -> Path:
    """Return the absolute path of a scenario's current live frame."""
    return _artifacts_dir() / live_frame_path(scenario_id)


def _enabled_from_environment() -> bool:
    value = os.environ.get("SIMKIT_LIVE_FRAMES", "")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _size_from_environment() -> tuple[int, int]:
    value = os.environ.get("SIMKIT_LIVE_SIZE")
    if not value:
        return DEFAULT_LIVE_SIZE
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", value)
    if match is None:
        return DEFAULT_LIVE_SIZE
    width, height = (int(part) for part in match.groups())
    if width < 1 or height < 1:
        return DEFAULT_LIVE_SIZE
    return width, height


class LiveFrameWriter:
    """Wall-clock throttled writer for one scenario's latest JPEG."""

    def __init__(
        self,
        scenario_id: str,
        *,
        fps: float | None = None,
        size: tuple[int, int] | None = None,
        camera: str | int | None = None,
        destination: str | Path | None = None,
        progress_path: str | Path | None = None,
    ) -> None:
        self.scenario_id = str(scenario_id)
        self.fps = self._parse_fps(fps)
        self.width, self.height = size or _size_from_environment()
        self.camera = camera
        self._destination = Path(destination) if destination is not None else None
        self._progress_path = Path(progress_path) if progress_path is not None else None
        self._progress: float | None = None
        self._sim_time_s = 0.0
        self._renderer: Any = None
        self._last_capture: float | None = None
        self._disabled = not _enabled_from_environment() or self.fps <= 0
        self._closed = False
        self._frames = 0
        self.drops = 0

    @staticmethod
    def _parse_fps(fps: float | None) -> float:
        if fps is not None:
            try:
                return float(fps)
            except (TypeError, ValueError):
                return DEFAULT_LIVE_FPS
        value = os.environ.get("SIMKIT_LIVE_FPS")
        if value is None:
            return DEFAULT_LIVE_FPS
        try:
            return float(value)
        except ValueError:
            return DEFAULT_LIVE_FPS

    @property
    def enabled(self) -> bool:
        """Whether captures can still be attempted."""
        return not self._disabled and not self._closed

    @property
    def rel_path(self) -> str:
        if self._destination is None:
            return live_frame_path(self.scenario_id)
        try:
            return self._destination.resolve().relative_to(_artifacts_dir()).as_posix()
        except ValueError:
            return self._destination.as_posix()

    @property
    def has_frame(self) -> bool:
        """Whether at least one frame has been atomically published."""
        return self._frames > 0

    def set_progress(self, progress: float | None, sim_time_s: float) -> None:
        """Set the values published alongside the next successful capture."""
        self._progress = progress
        self._sim_time_s = float(sim_time_s)

    def maybe_capture(self, scene: Any, *, force: bool = False) -> bool:
        """Write a frame when due, degrading permanently on any failure."""
        if not self.enabled:
            return False
        now = time.monotonic()
        interval = 1.0 / self.fps
        if (
            not force
            and self._last_capture is not None
            and now - self._last_capture < interval
        ):
            return False
        self._last_capture = now
        temporary: Path | None = None
        progress_temporary: Path | None = None
        try:
            renderer = self._ensure_renderer(scene)
            camera = self.camera if self.camera is not None else -1
            renderer.update_scene(scene.data, camera)
            frame = renderer.render()
            import imageio.v2 as imageio

            destination = self._destination or live_frame_file(self.scenario_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
            imageio.imwrite(
                str(temporary),
                frame,
                format="JPEG",
                quality=85,
            )
            os.replace(temporary, destination)
            if self._progress_path is not None:
                self._progress_path.parent.mkdir(parents=True, exist_ok=True)
                progress_temporary = self._progress_path.with_name(
                    f"{self._progress_path.name}.{os.getpid()}.tmp"
                )
                progress_temporary.write_text(
                    json.dumps(
                        {
                            "progress": self._progress,
                            "sim_time_s": self._sim_time_s,
                        }
                    ),
                    encoding="utf-8",
                )
                os.replace(progress_temporary, self._progress_path)
            self._frames += 1
            return True
        except Exception:  # noqa: BLE001 - live rendering is best effort
            self.drops += 1
            if temporary is not None:
                with suppress(Exception):
                    temporary.unlink(missing_ok=True)
            if progress_temporary is not None:
                with suppress(Exception):
                    progress_temporary.unlink(missing_ok=True)
            self._disable()
            return False

    def _ensure_renderer(self, scene: Any) -> Any:
        if self._renderer is not None:
            return self._renderer
        from simkit.recorder import require_headless_gl

        require_headless_gl()
        self._renderer = mujoco.Renderer(
            scene.model,
            height=self.height,
            width=self.width,
        )
        return self._renderer

    def _disable(self) -> None:
        self._disabled = True
        renderer, self._renderer = self._renderer, None
        if renderer is not None:
            try:
                closer = getattr(renderer, "close", None)
                if callable(closer):
                    closer()
            except Exception:  # noqa: BLE001 - cleanup is best effort
                return

    def close(self) -> None:
        """Release rendering resources and remove the transient frame."""
        if self._closed:
            return
        self._closed = True
        self._disable()
        try:
            (self._destination or live_frame_file(self.scenario_id)).unlink(
                missing_ok=True
            )
            if self._progress_path is not None:
                self._progress_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            return
