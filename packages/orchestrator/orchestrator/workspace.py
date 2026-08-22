"""Clone, check out and mutate the customer repo on local disk.

Responsibility
--------------
Give every run an isolated working copy of the robot control code at the exact
pushed SHA, and give agents a place to write patches without touching each
other's work.

Inputs:  ``owner/name``, a commit SHA, a ``GITHUB_TOKEN``.
Outputs: a directory path, plus branch/diff helpers built on ``gh`` and ``git``.

Isolation model
---------------
No Docker on this machine, so isolation is by directory, not container:

    workspaces/<run_id>/base/        <- pristine checkout at the pushed SHA
    workspaces/<run_id>/fix-<cid>/   <- one worktree per Fixer agent
    workspaces/<run_id>/verify/      <- merged patches, for the full-suite gate

Per-cluster worktrees matter: two Fixers editing one checkout would corrupt
each other's diffs, and the resulting mess would look like a robot bug rather
than an orchestration bug. ``git worktree`` gives cheap parallel checkouts off
one clone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Workspace:
    """A run's checkout tree on local disk."""

    run_id: str
    repo: str
    commit_sha: str
    root: Path

    @property
    def base(self) -> Path:
        """Pristine checkout at the pushed SHA. Never written to."""
        return self.root / "base"

    def worktree(self, name: str) -> Path:
        """Path to a named parallel checkout (one per Fixer)."""
        return self.root / name


async def clone(repo: str, commit_sha: str, run_id: str, dest_root: Path) -> Workspace:
    """Shallow-clone ``repo`` via ``gh repo clone`` and check out ``commit_sha``.

    Uses ``gh`` rather than raw git so authentication comes from GITHUB_TOKEN
    without writing credentials into a remote URL.
    """
    raise NotImplementedError
    # TODO(build): gh repo clone --depth, git fetch origin <sha>, git checkout.


async def create_worktree(ws: Workspace, name: str, branch: str) -> Path:
    """Add a ``git worktree`` so an agent can work in isolation."""
    raise NotImplementedError
    # TODO(build): git worktree add -b <branch> <path> <sha>.


async def read_config(ws: Workspace) -> dict:
    """Parse ``robotci.yaml`` from the checkout root.

    Returns ``{}`` when absent — a missing config is not an error, it means
    every field is inferred. See ``robotci.example.yaml``.
    """
    raise NotImplementedError
    # TODO(build): yaml.safe_load, validate against a schema, apply defaults.


async def diff(ws: Workspace, worktree: str) -> str:
    """Unified diff of a worktree against the base SHA."""
    raise NotImplementedError
    # TODO(build): git diff <sha>..HEAD in the worktree.


async def apply_patch(ws: Workspace, worktree: str, patch: str) -> None:
    """Apply a unified diff produced by an agent."""
    raise NotImplementedError
    # TODO(build): git apply --3way, surface conflicts as a raised error.


async def merge_patches(ws: Workspace, worktrees: list[str], into: str) -> list[str]:
    """Combine every Fixer's branch into the verify worktree.

    Returns the list of worktree names that conflicted. Conflicts are a real
    outcome, not an exception: two agents fixing overlapping code is exactly
    what the Reviewer role exists to adjudicate.
    """
    raise NotImplementedError
    # TODO(build): sequential git merge, collect conflicts, abort on failure.


async def cleanup(ws: Workspace, keep_artifacts: bool = True) -> None:
    """Remove worktrees and the clone after a run finishes."""
    raise NotImplementedError
    # TODO(build): git worktree remove per tree, then rmtree the root.
