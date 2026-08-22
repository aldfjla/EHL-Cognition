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

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Depth of the initial clone. Enough history for the pushed SHA and its parent
#: (which the diff in the report is taken against), not the whole repo.
CLONE_DEPTH = 50


class GitError(RuntimeError):
    """A git or gh invocation failed. Infrastructure, never a robot failure."""


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


@dataclass(frozen=True)
class PatchConflict:
    """A patch rejected because an already-merged sibling touched its files."""

    worktree: str
    branch: str
    sha: str
    files: tuple[str, ...]
    blocked_by: tuple[str, ...]


async def run_git(cwd: Path, *args: str, check: bool = True) -> str:
    """Run one git command in ``cwd`` and return its stdout.

    Raises :class:`GitError` on a non-zero exit unless ``check`` is false.
    """
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    if check and process.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {cwd} "
            f"({process.returncode}): {err.decode().strip()}"
        )
    return out.decode()


async def clone(repo: str, commit_sha: str, run_id: str, dest_root: Path) -> Workspace:
    """Shallow-clone ``repo`` via ``gh repo clone`` and check out ``commit_sha``.

    Uses ``gh`` rather than raw git so authentication comes from GITHUB_TOKEN
    without writing credentials into a remote URL.
    """
    root = Path(dest_root) / run_id
    ws = Workspace(run_id=run_id, repo=repo, commit_sha=commit_sha, root=root)
    if ws.base.exists():
        shutil.rmtree(ws.base)
    ws.base.mkdir(parents=True, exist_ok=True)

    process = await asyncio.create_subprocess_exec(
        "gh",
        "repo",
        "clone",
        repo,
        str(ws.base),
        "--",
        "--depth",
        str(CLONE_DEPTH),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await process.communicate()
    if process.returncode != 0:
        raise GitError(f"gh repo clone {repo} failed: {err.decode().strip()}")

    await run_git(ws.base, "fetch", "--depth", str(CLONE_DEPTH), "origin", commit_sha)
    await run_git(ws.base, "checkout", "--detach", commit_sha)
    return ws


async def create_worktree(ws: Workspace, name: str, branch: str) -> Path:
    """Add a ``git worktree`` so an agent can work in isolation."""
    path = ws.worktree(name)
    if path.exists():
        await run_git(ws.base, "worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path)
    await run_git(ws.base, "branch", "-D", branch, check=False)
    await run_git(ws.base, "worktree", "add", "-b", branch, str(path), ws.commit_sha)
    return path


async def read_config(ws: Workspace) -> dict:
    """Parse ``robotci.yaml`` from the checkout root.

    Returns ``{}`` when absent — a missing config is not an error, it means
    every field is inferred. See ``robotci.example.yaml``.
    """
    for name in ("robotci.yaml", "robotci.yml"):
        path = ws.base / name
        if path.exists():
            loaded = yaml.safe_load(path.read_text()) or {}
            if not isinstance(loaded, dict):
                raise GitError(f"{name} must be a mapping, got {type(loaded).__name__}")
            return _apply_defaults(loaded)
    return {}


async def diff(ws: Workspace, worktree: str) -> str:
    """Unified diff of a worktree against the base SHA."""
    path = ws.worktree(worktree) if worktree != "base" else ws.base
    return await run_git(path, "diff", ws.commit_sha)


async def apply_patch(ws: Workspace, worktree: str, patch: str) -> None:
    """Apply a unified diff produced by an agent."""
    path = ws.worktree(worktree)
    process = await asyncio.create_subprocess_exec(
        "git",
        "apply",
        "--3way",
        "-",
        cwd=str(path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await process.communicate(patch.encode())
    if process.returncode != 0:
        raise GitError(f"patch did not apply in {worktree}: {err.decode().strip()}")


async def merge_patches(
    ws: Workspace, worktrees: list[str], into: str
) -> list[PatchConflict]:
    """Combine every Fixer's branch into the verify worktree.

    Returns structured conflict records. Conflicts are a real outcome, not an
    exception: two agents fixing overlapping code is exactly what the Reviewer
    role exists to adjudicate.
    """
    target = ws.worktree(into)
    if not target.exists():
        await create_worktree(ws, into, f"robotci/verify-{ws.commit_sha[:7]}")

    conflicted: list[PatchConflict] = []
    merged_files: dict[str, set[str]] = {}
    for name in worktrees:
        sha = (await run_git(ws.worktree(name), "rev-parse", "HEAD")).strip()
        branch = (
            await run_git(ws.worktree(name), "rev-parse", "--abbrev-ref", "HEAD")
        ).strip()
        process = await asyncio.create_subprocess_exec(
            "git",
            "merge",
            "--no-edit",
            sha,
            cwd=str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        if process.returncode != 0:
            files = tuple(
                line
                for line in (
                    await run_git(target, "diff", "--name-only", "--diff-filter=U")
                ).splitlines()
                if line
            )
            blocked_by = tuple(
                sibling
                for sibling, sibling_files in merged_files.items()
                if set(files) & sibling_files
            )
            if not blocked_by:
                for sibling_path in ws.root.iterdir():
                    sibling = sibling_path.name
                    if sibling in {name, into, "base"} or not sibling_path.is_dir():
                        continue
                    try:
                        sibling_sha = (
                            await run_git(sibling_path, "rev-parse", "HEAD")
                        ).strip()
                        await run_git(
                            target, "merge-base", "--is-ancestor", sibling_sha, "HEAD"
                        )
                    except (GitError, FileNotFoundError):
                        continue
                    sibling_files = {
                        line
                        for line in (
                            await run_git(
                                target,
                                "diff",
                                "--name-only",
                                f"{ws.commit_sha}..{sibling_sha}",
                            )
                        ).splitlines()
                        if line
                    }
                    if set(files) & sibling_files:
                        blocked_by += (sibling,)
            conflicted.append(
                PatchConflict(
                    worktree=name,
                    branch=branch,
                    sha=sha,
                    files=files,
                    blocked_by=blocked_by,
                )
            )
            await run_git(target, "merge", "--abort", check=False)
            continue
        merged = {
            line
            for line in (
                await run_git(
                    target,
                    "diff",
                    "--name-only",
                    f"{ws.commit_sha}..HEAD",
                )
            ).splitlines()
            if line
        }
        merged_files[name] = merged
    return conflicted


async def cleanup(ws: Workspace, keep_artifacts: bool = True) -> None:
    """Remove worktrees and the clone after a run finishes."""
    listing = await run_git(ws.base, "worktree", "list", "--porcelain", check=False)
    for line in listing.splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line.removeprefix("worktree ").strip())
        if path != ws.base:
            await run_git(
                ws.base, "worktree", "remove", "--force", str(path), check=False
            )
    if keep_artifacts:
        shutil.rmtree(ws.base, ignore_errors=True)
    else:
        shutil.rmtree(ws.root, ignore_errors=True)


def _apply_defaults(config: dict) -> dict:
    """Fill the fields the pipeline reads unconditionally.

    Anything not defaulted here stays absent, which is how the agents know a
    value is theirs to infer.
    """
    config.setdefault("version", 1)
    config.setdefault("robot", {})
    config.setdefault("control", {}).setdefault("rate_hz", 100)
    config.setdefault("task", {}).setdefault("name", "task")
    scenarios = config.setdefault("scenarios", {})
    scenarios.setdefault("count", 50)
    scenarios.setdefault("seed", 1337)
    scenarios.setdefault("randomize", {})
    policy = config.setdefault("policy", {})
    policy.setdefault("pass_threshold", 1.0)
    policy.setdefault("max_fix_iterations", 3)
    policy.setdefault("open_pull_request", True)
    policy.setdefault("record_video", "failures")
    return config
