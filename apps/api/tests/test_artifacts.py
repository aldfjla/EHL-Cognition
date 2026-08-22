"""Artifact serving, including the traversal check.

``ARTIFACTS_DIR`` sits inside the repo, so an unchecked relative path would hand
out ``.env``. That case is tested explicitly.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings


def write_artifact(rel_path: str, content: bytes = b"data") -> Path:
    path = get_settings().artifacts_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_video_is_served_with_range_support(client: TestClient) -> None:
    """The dashboard's <video> element needs byte ranges to seek."""
    write_artifact("run_1/scn_03_a1.mp4", b"\x00\x00\x00\x18ftyp")

    response = client.get("/artifacts/video/run_1/scn_03_a1.mp4")

    assert response.status_code == 200
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_report_and_diff(client: TestClient) -> None:
    write_artifact("run_1/report.md", b"# 2 failures fixed")
    write_artifact("run_1/patch.diff", b"--- a/ctrl.py")

    assert client.get("/artifacts/report/run_1").text == "# 2 failures fixed"
    assert client.get("/artifacts/diff/run_1").text == "--- a/ctrl.py"


def test_missing_artifact_is_404(client: TestClient) -> None:
    assert client.get("/artifacts/video/run_1/nope.mp4").status_code == 404


def test_traversal_is_refused(client: TestClient) -> None:
    (get_settings().artifacts_dir.parent / "secret.env").write_text("KEY=1")

    for path in ("../secret.env", "run_1/../../secret.env"):
        response = client.get(f"/artifacts/{path}")
        assert response.status_code in {400, 404}
        assert "KEY=1" not in response.text
