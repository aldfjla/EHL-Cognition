"""Serve generated artifacts: videos, reports and diffs.

Responsibility
--------------
Expose ``ARTIFACTS_DIR`` over HTTP so the dashboard can play clips and the PR
body can link to evidence.

Inputs:  an artifact path relative to ``ARTIFACTS_DIR``.
Outputs: file responses with correct content types and range support.

Security
--------
This router turns a filesystem into a public URL space, so path containment is
not optional: resolve the requested path and confirm it is still inside
``ARTIFACTS_DIR`` before opening anything. ``../`` traversal here would serve
the ``.env`` file, Devin key included.

Video specifically needs HTTP range support — browsers issue range requests for
``<video>`` and a plain FileResponse makes seeking fail in a way that looks like
a broken recording rather than a broken server.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def safe_path(rel_path: str) -> Path:
    """Resolve ``rel_path`` under ARTIFACTS_DIR, rejecting escapes.

    Raises ``HTTPException(400)`` if the resolved path leaves the directory.
    """
    root = get_settings().artifacts_dir.resolve()
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=400, detail="path escapes ARTIFACTS_DIR")
    return candidate


def _file_or_404(rel_path: str, media_type: str) -> FileResponse:
    """A FileResponse for a contained path, 404 when the artifact is absent."""
    path = safe_path(rel_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    # Starlette's FileResponse answers Range requests itself, which is what
    # makes seeking work in a <video> element.
    return FileResponse(path, media_type=media_type, headers={"Accept-Ranges": "bytes"})


@router.get("/video/{run_id}/{name}")
async def get_video(run_id: str, name: str) -> FileResponse:
    """Stream a scenario mp4 with range support."""
    return _file_or_404(f"{run_id}/{name}", "video/mp4")


@router.get("/report/{run_id}")
async def get_report_markdown(run_id: str) -> FileResponse:
    """The rendered incident report as markdown."""
    return _file_or_404(f"{run_id}/report.md", "text/markdown")


@router.get("/diff/{run_id}")
async def get_diff(run_id: str) -> FileResponse:
    """The unified diff of all accepted patches."""
    return _file_or_404(f"{run_id}/patch.diff", "text/plain")


@router.get("/{rel_path:path}")
async def get_artifact(rel_path: str) -> FileResponse:
    """Any artifact by its stored path.

    Events carry paths relative to ``ARTIFACTS_DIR`` (``artifact.created``), and
    the client resolves them with ``api.artifactUrl()`` — this is the route that
    URL lands on. Content type is guessed from the suffix; containment is still
    enforced by :func:`safe_path`.
    """
    media_type = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
    return _file_or_404(rel_path, media_type)
