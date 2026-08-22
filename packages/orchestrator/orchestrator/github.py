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

from orchestrator.schemas import Report, Run


def branch_name(run: Run) -> str:
    """Deterministic branch name, e.g. ``robotci/fix-a1b2c3d``.

    Deterministic so a re-run of the same commit reuses the branch instead of
    littering the customer's repo with near-identical branches.
    """
    raise NotImplementedError
    # TODO(build): f"robotci/fix-{run.commit_sha[:7]}".


async def push_branch(repo_dir: str, branch: str, message: str) -> str:
    """Commit the working tree and push the branch. Returns the new SHA."""
    raise NotImplementedError
    # TODO(build): git add -A, commit with a co-authored-by trailer naming the
    # agents, git push -u origin <branch>.


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
    raise NotImplementedError
    # TODO(build): gh pr create --title report.title --body-file <rendered md>.


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
    raise NotImplementedError
    # TODO(build): POST /repos/{repo}/statuses/{sha} with context "robot-ci".


async def comment_on_commit(repo: str, sha: str, body: str) -> None:
    """Post the summary as a commit comment when no PR is opened."""
    raise NotImplementedError
    # TODO(build): gh api repos/{repo}/commits/{sha}/comments.


# TODO(build): add a DRY_RUN guard read from config that logs every outbound
# write instead of performing it — required before pointing this at a repo
# anyone cares about.
