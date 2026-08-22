"""Workspace layout, config parsing and real git worktrees."""

from __future__ import annotations

from pathlib import Path

import pytest
from orchestrator import workspace as ws_mod
from orchestrator.workspace import (
    GitError,
    PatchConflict,
    Workspace,
    read_config,
    run_git,
)


async def make_repo(tmp_path: Path) -> Workspace:
    """A real git repo standing in for a cloned customer checkout."""
    ws = Workspace(
        run_id="run_1", repo="acme/arm-control", commit_sha="", root=tmp_path / "run_1"
    )
    ws.base.mkdir(parents=True)
    await run_git(ws.base, "init", "-q", "-b", "main")
    await run_git(ws.base, "config", "user.email", "robot-ci@devin.ai")
    await run_git(ws.base, "config", "user.name", "Robot CI")
    (ws.base / "control.py").write_text("grip_force = 1.0\n")
    await run_git(ws.base, "add", "control.py")
    await run_git(ws.base, "commit", "-q", "-m", "initial")
    sha = (await run_git(ws.base, "rev-parse", "HEAD")).strip()
    return Workspace(run_id=ws.run_id, repo=ws.repo, commit_sha=sha, root=ws.root)


def test_layout_separates_the_clone_from_worktrees(tmp_path: Path) -> None:
    ws = Workspace(run_id="run_1", repo="a/b", commit_sha="c" * 40, root=tmp_path)
    assert ws.base.parent == tmp_path
    assert ws.worktree("fix-cls_1") != ws.base
    assert ws.worktree("fix-cls_1") != ws.worktree("verify")


async def test_read_config_returns_empty_dict_when_absent(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    assert await read_config(ws) == {}


async def test_read_config_parses_yaml_and_applies_defaults(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    (ws.base / "robotci.yaml").write_text(
        "robot:\n  menagerie: franka_emika_panda\nscenarios:\n  count: 8\n"
    )
    config = await read_config(ws)

    assert config["robot"]["menagerie"] == "franka_emika_panda"
    assert config["scenarios"]["count"] == 8
    # Defaults the pipeline reads unconditionally are filled in.
    assert config["scenarios"]["seed"]
    assert config["control"]["rate_hz"]


async def test_read_config_rejects_a_non_mapping(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    (ws.base / "robotci.yaml").write_text("- not\n- a mapping\n")
    with pytest.raises(GitError, match="must be a mapping"):
        await read_config(ws)


async def test_worktrees_are_isolated_and_diffable(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    one = await ws_mod.create_worktree(ws, "fix-cls_1", "robotci/fix-cls_1")
    two = await ws_mod.create_worktree(ws, "fix-cls_2", "robotci/fix-cls_2")

    (one / "control.py").write_text("grip_force = 2.0\n")
    assert (two / "control.py").read_text() == "grip_force = 1.0\n"

    patch = await ws_mod.diff(ws, "fix-cls_1")
    assert "grip_force = 2.0" in patch
    assert await ws_mod.diff(ws, "fix-cls_2") == ""


async def test_apply_patch_replays_a_diff_into_another_worktree(
    tmp_path: Path,
) -> None:
    ws = await make_repo(tmp_path)
    source = await ws_mod.create_worktree(ws, "fix-cls_1", "robotci/fix-cls_1")
    (source / "control.py").write_text("grip_force = 2.0\n")
    patch = await ws_mod.diff(ws, "fix-cls_1")

    target = await ws_mod.create_worktree(ws, "verify", "robotci/verify")
    await ws_mod.apply_patch(ws, "verify", patch)

    assert (target / "control.py").read_text() == "grip_force = 2.0\n"


async def test_merge_patches_reports_conflicts_instead_of_raising(
    tmp_path: Path,
) -> None:
    ws = await make_repo(tmp_path)
    for name in ("fix-a", "fix-b"):
        path = await ws_mod.create_worktree(ws, name, f"robotci/{name}")
        (path / "control.py").write_text(f"grip_force = {name!r}\n")
        await run_git(path, "commit", "-aqm", f"fix from {name}")

    conflicts = await ws_mod.merge_patches(
        ws, ["fix-a", "fix-b"], into="verify", landed_worktrees=[]
    )

    # Both touch the same line: the first lands, the second conflicts and is
    # aborted rather than left half-merged.
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], PatchConflict)
    assert conflicts[0].worktree == "fix-b"
    assert conflicts[0].files == ("control.py",)
    assert conflicts[0].blocked_by == ("fix-a",)
    verify = ws.worktree("verify")
    assert "fix-a" in (verify / "control.py").read_text()
    state = await run_git(verify, "status", "--porcelain")
    assert state.strip() == ""


async def test_merge_patches_attributes_landed_sibling_in_one_at_a_time_flow(
    tmp_path: Path,
) -> None:
    ws = await make_repo(tmp_path)
    for name in ("fix-a", "fix-b"):
        path = await ws_mod.create_worktree(ws, name, f"robotci/{name}")
        (path / "control.py").write_text(f"grip_force = {name!r}\n")
        await run_git(path, "commit", "-aqm", f"fix from {name}")

    assert (
        await ws_mod.merge_patches(ws, ["fix-a"], into="verify", landed_worktrees=[])
        == []
    )
    conflicts = await ws_mod.merge_patches(
        ws, ["fix-b"], into="verify", landed_worktrees=["fix-a"]
    )

    assert len(conflicts) == 1
    assert conflicts[0].files == ("control.py",)
    assert conflicts[0].blocked_by == ("fix-a",)
    assert "fix-a" in (ws.worktree("verify") / "control.py").read_text()
    assert (await run_git(ws.worktree("verify"), "status", "--porcelain")).strip() == ""


async def test_non_conflicting_fixes_all_land(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    for name, filename in (("fix-a", "a.py"), ("fix-b", "b.py")):
        path = await ws_mod.create_worktree(ws, name, f"robotci/{name}")
        (path / filename).write_text("patched\n")
        await run_git(path, "add", filename)
        await run_git(path, "commit", "-qm", f"fix from {name}")

    assert (
        await ws_mod.merge_patches(
            ws, ["fix-a", "fix-b"], into="verify", landed_worktrees=[]
        )
        == []
    )
    verify = ws.worktree("verify")
    assert (verify / "a.py").exists()
    assert (verify / "b.py").exists()


async def test_same_file_different_regions_both_land(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    (ws.base / "control.py").write_text(
        "\n".join(f"line_{index} = {index}" for index in range(12)) + "\n"
    )
    await run_git(ws.base, "add", "control.py")
    await run_git(ws.base, "commit", "-qm", "add distant regions")
    ws.commit_sha = (await run_git(ws.base, "rev-parse", "HEAD")).strip()
    for name, text in (
        (
            "fix-a",
            "\n".join(["line_0 = 2"] + [f"line_{i} = {i}" for i in range(1, 12)])
            + "\n",
        ),
        (
            "fix-b",
            "\n".join([f"line_{i} = {i}" for i in range(11)] + ["line_11 = 22"]) + "\n",
        ),
    ):
        path = await ws_mod.create_worktree(ws, name, f"robotci/{name}")
        (path / "control.py").write_text(text)
        await run_git(path, "add", "control.py")
        await run_git(path, "commit", "-qm", f"fix from {name}")

    assert (
        await ws_mod.merge_patches(
            ws, ["fix-a", "fix-b"], into="verify", landed_worktrees=[]
        )
        == []
    )
    content = (ws.worktree("verify") / "control.py").read_text()
    assert "line_0 = 2" in content
    assert "line_11 = 22" in content


async def test_cleanup_removes_the_clone_and_its_worktrees(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    worktree = await ws_mod.create_worktree(ws, "fix-a", "robotci/fix-a")

    await ws_mod.cleanup(ws, keep_artifacts=False)

    assert not worktree.exists()
    assert not ws.base.exists()
    assert not ws.root.exists()


async def test_run_git_can_report_failure_without_raising(tmp_path: Path) -> None:
    ws = await make_repo(tmp_path)
    assert await run_git(ws.base, "rev-parse", "nope", check=False) is not None
    with pytest.raises(GitError):
        await run_git(ws.base, "rev-parse", "nope")
