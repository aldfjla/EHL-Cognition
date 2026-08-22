"""GitHub side effects: branches, pull requests and commit status checks.

Responsibility
--------------
Everything Robot CI writes back to the customer repo. Isolated in one module so
the blast radius of a bug is one file, and so a dry-run mode can neuter every
outbound write during rehearsal.

Inputs:  a :class:`~orchestrator.workspace.Workspace`, a
         :class:`~orchestrator.schemas.Report`, ``GITHUB_TOKEN``.
Outputs: a pushed branch, an open PR url, a commit status on the pushed SHA.

Implementation note: ``gh`` for anything with a CLI equivalent (auth is already
solved), ``httpx`` against the REST API for status checks, which ``gh`` exposes
awkwardly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import httpx

from orchestrator.schemas import Report, Run

log = logging.getLogger(__name__)

#: Commit status context, so the check reads as ours in the GitHub UI.
STATUS_CONTEXT = "robot-ci"

#: Trailer crediting the sessions that wrote the patch, so the customer's git
#: history says an agent did this rather than a mystery bot.
COAUTHOR_TRAILER = "Co-Authored-By: Robot CI <robot-ci@devin.ai>"

_VALID_STATES = ("pending", "success", "failure", "error")


class GitHubError(RuntimeError):
    """An outbound GitHub write failed. Infrastructure, not a robot failure."""


def dry_run() -> bool:
    """True when every outbound write should be logged instead of performed.

    Read from the environment rather than passed through every call site: a
    rehearsal must not depend on one caller remembering to thread a flag.
    """
    return os.getenv("ROBOTCI_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def branch_name(run: Run) -> str:
    """Deterministic branch name, e.g. ``robotci/fix-a1b2c3d``.

    Deterministic so a re-run of the same commit reuses the branch instead of
    littering the customer's repo with near-identical branches.
    """
    return f"robotci/fix-{run.commit_sha[:7]}"


async def push_branch(repo_dir: str, branch: str, message: str) -> str:
    """Commit the working tree and push the branch. Returns the new SHA."""
    directory = Path(repo_dir)
    body = f"{message}\n\n{COAUTHOR_TRAILER}\n"

    if dry_run():
        log.info("DRY_RUN: would commit and push %s from %s", branch, repo_dir)
        return await _git(directory, "rev-parse", "HEAD")

    await _git(directory, "checkout", "-B", branch)
    await _git(directory, "add", "-A")
    status = await _git(directory, "status", "--porcelain")
    if status.strip():
        await _git(directory, "commit", "-m", body)
    await _git(directory, "push", "--force-with-lease", "-u", "origin", branch)
    return await _git(directory, "rev-parse", "HEAD")


async def open_pull_request(
    repo: str,
    head: str,
    base: str,
    report: Report,
    draft: bool = False,
) -> str:
    """Open a PR whose body is the incident report. Returns the PR url.

    The report *is* the PR body — no separate summary is generated, so what the
    dashboard shows and what a human reviewer reads are the same text.
    """
    body = render_pr_body(report)
    if dry_run():
        log.info(
            "DRY_RUN: would open PR %s -> %s on %s: %s", head, base, repo, report.title
        )
        return f"https://github.com/{repo}/pull/DRY_RUN"

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_file = handle.name
    args = [
        "pr",
        "create",
        "--repo",
        repo,
        "--head",
        head,
        "--base",
        base,
        "--title",
        report.title,
        "--body-file",
        body_file,
    ]
    if draft:
        args.append("--draft")
    try:
        out = await _gh(*args)
    finally:
        os.unlink(body_file)
    url = out.strip().splitlines()[-1] if out.strip() else ""
    if not url.startswith("http"):
        raise GitHubError(f"could not read a PR url out of gh output: {out!r}")
    return url


async def set_commit_status(
    repo: str,
    sha: str,
    state: str,
    description: str,
    target_url: str | None = None,
) -> None:
    """Publish a commit status so the run shows up as a check on GitHub.

    ``state`` is one of ``pending|success|failure|error``. ``target_url`` should
    deep-link to the dashboard run page — that link is how a developer gets
    from a red check to the video of their robot failing.
    """
    if state not in _VALID_STATES:
        raise ValueError(f"state must be one of {_VALID_STATES}, got {state!r}")
    payload: dict[str, str] = {
        "state": state,
        "description": description[:140],
        "context": STATUS_CONTEXT,
    }
    if target_url:
        payload["target_url"] = target_url

    if dry_run():
        log.info("DRY_RUN: would set %s status on %s@%s", state, repo, sha[:7])
        return

    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise GitHubError("GITHUB_TOKEN unset; cannot publish a commit status")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo}/statuses/{sha}",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if response.status_code >= 300:
        raise GitHubError(
            f"commit status failed ({response.status_code}): {response.text}"
        )


async def comment_on_commit(repo: str, sha: str, body: str) -> None:
    """Post the summary as a commit comment when no PR is opened."""
    if dry_run():
        log.info("DRY_RUN: would comment on %s@%s", repo, sha[:7])
        return
    await _gh(
        "api",
        "--method",
        "POST",
        f"repos/{repo}/commits/{sha}/comments",
        "-f",
        f"body={body}",
    )


def render_pr_body(report: Report) -> str:
    """The report as markdown. Used verbatim as the PR body."""
    lines = [report.summary.strip(), ""]
    if report.before and report.after:
        lines += [
            "| suite | passed | failed | pass rate |",
            "|---|---|---|---|",
            (
                f"| before | {report.before.passed} | {report.before.failed} "
                f"| {report.before.pass_rate:.0%} |"
            ),
            (
                f"| after | {report.after.passed} | {report.after.failed} "
                f"| {report.after.pass_rate:.0%} |"
            ),
            "",
        ]
    for incident in report.incidents:
        lines += [
            f"### {incident.title} ({incident.status})",
            "",
            f"**Root cause.** {incident.root_cause}",
            "",
            f"**Resolution.** {incident.resolution}",
            "",
        ]
        if incident.files_changed:
            lines += ["Files: " + ", ".join(incident.files_changed), ""]
        if incident.before_video or incident.after_video:
            after_video = (
                incident.after_video or "no verified after-video; proof is unavailable"
            )
            lines += [
                (
                    f"Video: before `{incident.before_video or 'unavailable'}`, "
                    f"after `{after_video}`"
                ),
                "",
            ]
        elif incident.status == "unresolved":
            lines += ["Video proof unavailable: no recorded before/after pair.", ""]
    return "\n".join(lines).strip() + "\n"


async def _git(cwd: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    if process.returncode != 0:
        raise GitHubError(f"git {' '.join(args)} failed: {err.decode().strip()}")
    return out.decode().strip()


async def _gh(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    if process.returncode != 0:
        raise GitHubError(f"gh {' '.join(args)} failed: {err.decode().strip()}")
    return out.decode()
