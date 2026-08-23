"""Branch naming, PR body rendering and the DRY_RUN guard."""

from __future__ import annotations

from typing import Self

import httpx
import pytest
from orchestrator import github
from orchestrator.schemas import Incident, Report, Run, SuiteStats, Verdict


def make_run() -> Run:
    return Run(
        repo="acme/arm-control",
        commit_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        branch="main",
    )


def make_report() -> Report:
    return Report(
        run_id="run_1",
        verdict=Verdict.FIXED,
        title="Fixed gripper slip on low-friction surfaces",
        summary="One root cause, one patch, suite green.",
        incidents=[
            Incident(
                cluster_id="cls_1",
                title="gripper slips",
                status="fixed",
                root_cause="grip force ignores friction",
                resolution="scale force by the friction estimate",
                files_changed=["src/grip.py"],
                before_video="artifacts/before.mp4",
                after_video="artifacts/after.mp4",
            )
        ],
        before=SuiteStats(total=10, passed=6, failed=4, pass_rate=0.6),
        after=SuiteStats(total=10, passed=10, failed=0, pass_rate=1.0),
    )


def test_branch_name_is_deterministic_and_short() -> None:
    run = make_run()
    assert github.branch_name(run) == "robotci/fix-a1b2c3d"
    assert github.branch_name(run) == github.branch_name(make_run())


def test_dry_run_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOTCI_DRY_RUN", raising=False)
    assert github.dry_run() is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("ROBOTCI_DRY_RUN", value)
        assert github.dry_run() is True
    monkeypatch.setenv("ROBOTCI_DRY_RUN", "0")
    assert github.dry_run() is False


async def test_dry_run_performs_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOTCI_DRY_RUN", "1")

    async def explode(*args: str) -> str:
        raise AssertionError("dry run must not shell out")

    monkeypatch.setattr(github, "_gh", explode)
    monkeypatch.setattr(github, "_git", explode)

    url = await github.open_pull_request(
        "acme/arm-control", "robotci/fix-a1b2c3d", "main", make_report()
    )
    assert url.endswith("/DRY_RUN")
    # Neither of these may touch the network or the repo.
    await github.set_commit_status("acme/arm-control", "a" * 40, "success", "ok")
    await github.comment_on_commit("acme/arm-control", "a" * 40, "hello")


async def test_set_commit_status_rejects_unknown_states() -> None:
    with pytest.raises(ValueError, match="state must be one of"):
        await github.set_commit_status("acme/x", "a" * 40, "greenish", "ok")


async def test_resolve_branch_head_returns_sha_and_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        200,
        json={
            "sha": "b" * 40,
            "commit": {"message": "tune the grasp\nwith more detail"},
        },
        request=httpx.Request("GET", "https://api.github.com"),
    )

    class FakeClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, *args: object, **kwargs: object) -> httpx.Response:
            return response

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    assert await github.resolve_branch_head("acme/arm-control", "main") == (
        "b" * 40,
        "tune the grasp",
    )


async def test_resolve_branch_head_maps_github_422_to_branch_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = httpx.Response(
        422,
        json={"message": "No commit found for SHA: missing-branch"},
        request=httpx.Request("GET", "https://api.github.com"),
    )

    class FakeClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, *args: object, **kwargs: object) -> httpx.Response:
            return response

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(github.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    with pytest.raises(github.GitHubError) as caught:
        await github.resolve_branch_head("acme/arm-control", "missing-branch")

    assert caught.value.status_code == 404
    assert str(caught.value) == (
        "GitHub repository or branch not found for acme/arm-control@missing-branch; "
        "check the connected repository name and branch"
    )


def test_render_pr_body_contains_the_before_after_evidence() -> None:
    body = github.render_pr_body(make_report())
    assert "before" in body and "after" in body
    assert "60%" in body and "100%" in body
    assert "grip force ignores friction" in body
    assert "src/grip.py" in body
    assert "artifacts/after.mp4" in body
    assert body.endswith("\n")


def test_render_pr_body_marks_missing_after_video_as_unverified() -> None:
    report = make_report()
    report.incidents[0].after_video = None
    body = github.render_pr_body(report)
    assert "no verified after-video; proof is unavailable" in body
    assert "artifacts/before.mp4" in body


def test_coauthor_trailer_credits_the_agents() -> None:
    assert github.COAUTHOR_TRAILER.startswith("Co-Authored-By:")
