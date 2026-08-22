"""Live JPEG and MJPEG feeds for running scenarios.

Responsibility
--------------
Serve the worker's one-JPEG side channel without holding database sessions or
file handles across awaits. The worker overwrites the configured frame in
place; this router emits only complete frames that changed since the previous
part.

Inputs:  run id, scenario id, and a relative ``live_frame_path``.
Outputs: cache-disabled JPEG responses or multipart MJPEG responses.

Security and lifetime rules
---------------------------
Frame paths pass through :func:`app.routers.artifacts.safe_path`, the same
containment boundary used by ordinary artifact routes. Every MJPEG generator
opens short-lived database sessions for status polling and releases its stream
slot in ``finally`` so disconnects cannot strand capacity.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from orchestrator.schemas import ScenarioStatus
from sqlmodel import Session

from app.config import get_settings
from app.routers.artifacts import safe_path
from app.store import repo
from app.store.db import session_scope

router = APIRouter(tags=["live"])

_JPEG_START: Final[bytes] = b"\xff\xd8"
_JPEG_END: Final[bytes] = b"\xff\xd9"
_TERMINAL_STATUSES: Final[frozenset[ScenarioStatus]] = frozenset(
    {ScenarioStatus.PASSED, ScenarioStatus.FAILED, ScenarioStatus.ERROR}
)


class LiveStreamLease:
    """One idempotent reservation held by an MJPEG generator."""

    def __init__(self, limiter: LiveStreamLimiter) -> None:
        self._limiter = limiter
        self._released = False

    def release(self) -> None:
        """Return this reservation, including when called more than once."""
        if not self._released:
            self._released = True
            self._limiter.release(self)


class LiveStreamLimiter:
    """A small synchronous cap for active MJPEG generators."""

    def __init__(self) -> None:
        self._leases: set[LiveStreamLease] = set()

    @property
    def active_count(self) -> int:
        """Number of slots currently held by stream generators."""
        return len(self._leases)

    def acquire(self) -> LiveStreamLease | None:
        """Claim one slot, returning ``None`` when the cap is reached."""
        if self.active_count >= get_settings().max_live_streams:
            return None
        lease = LiveStreamLease(self)
        self._leases.add(lease)
        return lease

    def release(self, lease: LiveStreamLease) -> None:
        """Release the specified reservation."""
        self._leases.discard(lease)

    def reset(self) -> None:
        """Testing hook for isolated app instances."""
        self._leases.clear()


live_stream_limiter = LiveStreamLimiter()


def _scenario_for_path(run_id: str, scenario_id: str, db: Session) -> Path:
    """Validate ownership and return the recorded frame path."""
    if repo.get_run(db, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    scenario = repo.get_scenario(db, scenario_id)
    if scenario is None or scenario.run_id != run_id:
        raise HTTPException(status_code=404, detail="scenario not found")
    if scenario.live_frame_path is None:
        raise HTTPException(status_code=404, detail="no live frame yet")
    return safe_path(scenario.live_frame_path)


def _read_complete_frame(path: Path) -> tuple[tuple[int, int], bytes] | None:
    """Read a stable, complete JPEG without retaining a file handle."""
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except FileNotFoundError:
        return None
    signature = (after.st_mtime_ns, after.st_size)
    if (
        (before.st_mtime_ns, before.st_size) != signature
        or len(data) < 4
        or not data.startswith(_JPEG_START)
        or not data.endswith(_JPEG_END)
    ):
        return None
    return signature, data


def _scenario_status(scenario_id: str) -> ScenarioStatus | None:
    """Poll status through a short-lived session owned by this call."""
    with session_scope() as db:
        scenario = repo.get_scenario(db, scenario_id)
    return scenario.status if scenario else None


def _part(frame: bytes) -> bytes:
    """Format one complete multipart MJPEG part."""
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n"
        + f"Content-Length: {len(frame)}\r\n\r\n".encode()
        + frame
        + b"\r\n"
    )


async def _mjpeg(
    scenario_id: str,
    frame_path: Path,
    *,
    lease: LiveStreamLease,
) -> AsyncIterator[bytes]:
    """Yield changed complete frames until terminal or idle."""
    settings = get_settings()
    interval = 1.0 / max(settings.live_stream_fps, 0.1)
    idle_timeout = max(settings.live_stream_idle_timeout_s, 0.0)
    last_signature: tuple[int, int] | None = None
    last_frame_at = time.monotonic()
    next_status_at = 0.0

    try:
        while time.monotonic() - last_frame_at < idle_timeout:
            now = time.monotonic()
            if now >= next_status_at:
                status = await asyncio.to_thread(_scenario_status, scenario_id)
                next_status_at = now + 1.0
                if status is None or status in _TERMINAL_STATUSES:
                    break

            result = await asyncio.to_thread(_read_complete_frame, frame_path)
            if result is not None:
                signature, frame = result
                if signature != last_signature:
                    last_signature = signature
                    last_frame_at = time.monotonic()
                    yield _part(frame)

            remaining_idle = idle_timeout - (time.monotonic() - last_frame_at)
            if remaining_idle <= 0:
                break
            await asyncio.sleep(min(interval, remaining_idle))
        yield b"--frame--\r\n"
    finally:
        lease.release()


@router.get("/runs/{run_id}/scenarios/{scenario_id}/live.jpg")
async def live_jpg(run_id: str, scenario_id: str) -> Response:
    """Return the current complete frame for a thumbnail."""
    with session_scope() as db:
        path = _scenario_for_path(run_id, scenario_id, db)
    result = await asyncio.to_thread(_read_complete_frame, path)
    if result is None:
        raise HTTPException(status_code=404, detail="live frame not available")
    return Response(
        content=result[1],
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/runs/{run_id}/scenarios/{scenario_id}/live.mjpg")
async def live_mjpg(run_id: str, scenario_id: str) -> StreamingResponse:
    """Stream changed frames, bounded by the configured cap and idle timeout."""
    with session_scope() as db:
        path = _scenario_for_path(run_id, scenario_id, db)
    lease = live_stream_limiter.acquire()
    if lease is None:
        raise HTTPException(
            status_code=503,
            detail="live stream capacity exhausted; retry shortly",
            headers={"Retry-After": "1"},
        )
    return StreamingResponse(
        _mjpeg(scenario_id, path, lease=lease),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )
