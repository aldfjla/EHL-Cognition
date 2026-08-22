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

from fastapi import APIRouter

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def safe_path(rel_path: str) -> str:
    """Resolve ``rel_path`` under ARTIFACTS_DIR, rejecting escapes.

    Raises ``HTTPException(400)`` if the resolved path leaves the directory.
    """
    raise NotImplementedError
    # TODO(build): (ARTIFACTS_DIR / rel).resolve(), then
    # is_relative_to(ARTIFACTS_DIR.resolve()) — reject otherwise.


@router.get("/video/{run_id}/{name}")
async def get_video(run_id: str, name: str):
    """Stream a scenario mp4 with range support."""
    raise NotImplementedError
    # TODO(build): safe_path, FileResponse with media_type video/mp4 and
    # Accept-Ranges; verify seeking works in Chrome before calling it done.


@router.get("/report/{run_id}")
async def get_report_markdown(run_id: str):
    """The rendered incident report as markdown."""
    raise NotImplementedError
    # TODO(build): safe_path to <run_id>/report.md, text/markdown.


@router.get("/diff/{run_id}")
async def get_diff(run_id: str):
    """The unified diff of all accepted patches."""
    raise NotImplementedError
    # TODO(build): safe_path to <run_id>/patch.diff, text/plain.
