"""Model resolution, validated against the real Menagerie checkout."""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import pytest
from simkit.models import generator, menagerie, resolver

MINIMAL_URDF = """<?xml version="1.0"?>
<robot name="two_link">
  <link name="base"/>
  <link name="upper"/>
  <link name="lower"/>
  <joint name="shoulder_pan" type="revolute">
    <parent link="base"/><child link="upper"/>
    <origin xyz="0 0 0.1"/><axis xyz="0 0 1"/>
    <limit lower="-2.0" upper="2.0" effort="90" velocity="2"/>
  </joint>
  <joint name="elbow" type="revolute">
    <parent link="upper"/><child link="lower"/>
    <origin xyz="0.25 0 0"/><axis xyz="0 1 0"/>
    <limit lower="-1.5" upper="1.5" effort="60" velocity="2"/>
  </joint>
</robot>
"""

VALID_MJCF = """<mujoco model="repo_arm">
  <worldbody>
    <body name="arm">
      <joint name="shoulder" type="hinge" axis="0 1 0" range="-1 1"/>
      <geom type="capsule" fromto="0 0 0 0.2 0 0" size="0.03" mass="1"/>
    </body>
  </worldbody>
  <actuator>
    <position name="shoulder_act" joint="shoulder" kp="20" ctrlrange="-1 1"/>
  </actuator>
</mujoco>
"""


# -- menagerie index -------------------------------------------------------- #


def test_index_is_built_from_real_files(menagerie_dir: Path) -> None:
    models = menagerie.index(menagerie_dir)
    assert len(models) > 30
    for model in models:
        path = menagerie.resolve_model_path(model, menagerie_dir)
        assert path.is_file(), f"{model.name} -> {path}"
        assert path.suffix == ".xml"


def test_panda_indexes_as_the_arm_not_the_hand(menagerie_dir: Path) -> None:
    """The canonical MJCF must be the robot, not a sibling gripper file."""
    panda = menagerie.get("franka_emika_panda", menagerie_dir)
    assert panda is not None
    assert panda.model_path.endswith("panda.xml")
    assert panda.kind == "arm"
    assert panda.dof == 9  # 7 arm joints + 2 finger joints
    assert panda.vendor.lower().startswith("franka")


def test_indexed_models_compile_in_mujoco(menagerie_dir: Path) -> None:
    for name in ("franka_emika_panda", "universal_robots_ur5e"):
        model = menagerie.get(name, menagerie_dir)
        assert model is not None
        path = menagerie.resolve_model_path(model, menagerie_dir)
        compiled = mujoco.MjModel.from_xml_path(str(path))
        assert compiled.njnt > 0


def test_index_is_cached_and_refreshable(menagerie_dir, tmp_path) -> None:
    cache = menagerie_dir / "index.json"
    models = menagerie.index(menagerie_dir)
    assert cache.is_file()
    payload = json.loads(cache.read_text())
    assert len(payload["models"]) == len(models)
    # The cache is what later lookups read, not the tree.
    assert menagerie.index(menagerie_dir) == models
    assert menagerie.index(menagerie_dir, refresh=True) == models


def test_search_and_count_joints(menagerie_dir: Path) -> None:
    hits = menagerie.search("ur5", menagerie_dir)
    assert hits and hits[0].name == "universal_robots_ur5e"
    panda = menagerie.get("franka_emika_panda", menagerie_dir)
    counted = menagerie.count_joints(menagerie.resolve_model_path(panda, menagerie_dir))
    assert counted == panda.dof


def test_match_kinematics_prefers_the_same_dof(menagerie_dir: Path) -> None:
    ranked = menagerie.match_kinematics(
        6, ["shoulder_pan", "shoulder_lift", "elbow", "wrist_1"], menagerie_dir
    )
    assert ranked
    assert all(score > 0 for _, score in ranked)
    assert [score for _, score in ranked] == sorted(
        (score for _, score in ranked), reverse=True
    )
    # A 6-DOF signature must not rank a quadruped over a 6-DOF arm.
    assert all(abs((model.dof or 0) - 6) <= 2 for model, _ in ranked)

    named = menagerie.match_kinematics(6, ["ur5e_shoulder_pan"], menagerie_dir, limit=5)
    assert any("ur5e" in model.name for model, _ in named)


def test_summary_for_prompt_lists_models(menagerie_dir: Path) -> None:
    summary = menagerie.summary_for_prompt(menagerie_dir)
    assert "franka_emika_panda" in summary
    assert len(summary.splitlines()) > 10


def test_missing_model_is_none(menagerie_dir: Path) -> None:
    assert menagerie.get("no_such_robot", menagerie_dir) is None


# -- resolver --------------------------------------------------------------- #


def test_explicit_menagerie_name_wins(menagerie_dir, tmp_path) -> None:
    resolution = resolver.resolve(
        tmp_path, {"robot": {"menagerie": "franka_emika_panda"}}
    )
    assert resolution.found
    assert resolution.source == "menagerie"
    assert resolution.confidence == 1.0
    assert Path(resolution.model_path).is_file()


def test_explicit_model_path_wins_over_identification(tmp_path, toy_arm) -> None:
    resolution = resolver.resolve(tmp_path, {"robot": {"model_path": str(toy_arm)}})
    assert resolution.found
    assert resolution.source == "repo"
    assert resolution.confidence == 1.0
    assert Path(resolution.model_path) == toy_arm


def test_driver_import_identifies_the_vendor(menagerie_dir, tmp_path) -> None:
    (tmp_path / "control.py").write_text(
        "import ur_rtde\n\ndef move():\n    return ur_rtde.RTDEControlInterface\n"
    )
    resolution = resolver.identify(tmp_path)
    assert resolution.found
    assert resolution.name == "universal_robots_ur5e"
    assert resolution.confidence >= 0.8
    assert "ur_rtde" in resolution.report


def test_unidentifiable_repo_reports_instead_of_guessing(tmp_path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n")
    resolution = resolver.identify(tmp_path)
    assert resolution.found is False
    assert resolution.report


def test_parse_urdf_and_find_urdf(tmp_path) -> None:
    (tmp_path / "desc").mkdir()
    urdf = tmp_path / "desc" / "arm.urdf"
    urdf.write_text(MINIMAL_URDF)
    assert resolver.find_urdf(tmp_path) == urdf
    parsed = resolver.parse_urdf(urdf)
    assert parsed["dof"] == 2
    assert parsed["joint_names"] == ["shoulder_pan", "elbow"]
    assert parsed["robot_name"] == "two_link"


def test_repo_mjcf_wins_and_rejects_scene_only_xml(tmp_path) -> None:
    (tmp_path / "scene.xml").write_text(
        "<mujoco><worldbody><geom type='plane' size='1 1 .1'/></worldbody></mujoco>"
    )
    robot = tmp_path / "robot.xml"
    robot.write_text(VALID_MJCF)

    resolution = resolver.resolve(tmp_path, {"robot": {}})

    assert resolution.found
    assert resolution.source == "repo"
    assert Path(resolution.model_path) == robot
    assert "scene.xml" not in resolution.provenance
    assert resolution.approximate is False


def test_scene_only_mjcf_is_rejected(tmp_path) -> None:
    (tmp_path / "scene.xml").write_text(
        "<mujoco><worldbody><geom type='plane' size='1 1 .1'/></worldbody></mujoco>"
    )

    resolution = resolver.resolve(tmp_path, {"robot": {}})

    assert resolution.found is False
    assert "contains no robot bodies" in resolution.report


def test_mjcf_dof_counts_robot_joints_not_free_payload_dofs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(generator, "validate", lambda _path: (True, "ok"))
    model = tmp_path / "payload.xml"
    model.write_text(
        VALID_MJCF.replace(
            "<worldbody>", '<option gravity="0 0 0"/>\n  <worldbody>'
        ).replace(
            "  </worldbody>",
            """    <body name="payload">
      <freejoint name="payload_free"/>
      <geom type="sphere" size="0.05" mass="1"/>
    </body>
  </worldbody>""",
        )
    )

    resolution = resolver.resolve(tmp_path, {"robot": {}})

    assert resolution.found
    assert resolution.dof == 1


def test_resolve_converts_a_repo_urdf(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    urdf = repo / "arm.urdf"
    urdf.write_text(MINIMAL_URDF)

    resolution = resolver.resolve(repo, {"robot": {}}, tmp_path / "output")

    assert resolution.found
    assert resolution.source == "repo"
    assert Path(resolution.model_path).is_file()
    assert resolution.processing_steps == [
        "URDF compile",
        "MJCF output validation",
    ]
    assert resolution.approximate is False


def test_failed_urdf_conversion_falls_through_honestly(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "broken.urdf").write_text("<robot name='broken'><link></robot>")

    resolution = resolver.resolve(repo, {"robot": {}}, tmp_path / "output")

    assert resolution.found is False
    assert "URDF conversion" in resolution.report
    assert "failed" in resolution.report


def test_readme_match_is_low_confidence_and_approximate(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "This controller drives a Franka Panda robot.\n"
    )

    resolution = resolver.identify(tmp_path)

    assert resolution.found
    assert resolution.name == "franka_emika_panda"
    assert resolution.confidence == 0.55
    assert resolution.approximate is True
    assert "README/docs" in resolution.report


def test_dependency_match_is_low_confidence_and_approximate(tmp_path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "franka @ https://example.invalid/franka\n"
    )

    resolution = resolver.identify(tmp_path)

    assert resolution.found
    assert resolution.name == "franka_emika_panda"
    assert resolution.confidence == 0.55
    assert resolution.approximate is True
    assert "dependency manifest" in resolution.report


def test_generic_readme_prose_does_not_identify_a_robot(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "This robot arm has a mobile base and a gripper.\n"
    )

    resolution = resolver.identify(tmp_path)

    assert resolution.found is False


def test_menagerie_license_is_read_from_the_model_directory(menagerie_dir) -> None:
    model = menagerie.get("franka_emika_panda", menagerie_dir)
    assert model is not None

    assert menagerie.read_license(model, menagerie_dir) == "Apache License 2.0"


def test_license_parser_keeps_versions_and_rejects_unknown_text(tmp_path) -> None:
    model = menagerie.MenagerieModel("example", "Example", "example.xml")
    directory = tmp_path / "example"
    directory.mkdir()
    license_file = directory / "LICENSE"
    license_file.write_text("GNU General Public License version 3, June 2007\n")
    assert menagerie.read_license(model, tmp_path) == (
        "GNU General Public License v3.0"
    )

    license_file.write_text("Copyright notice mentioning BSD as a trademark.\n")
    assert menagerie.read_license(model, tmp_path) is None


def test_resolution_cache_hit_and_fingerprint_change(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "arm.urdf").write_text(MINIMAL_URDF)
    output = tmp_path / "output"
    cache = tmp_path / "cache"

    first = resolver.resolve(
        repo, {"robot": {}}, output, cache, repo_identity="acme/arm"
    )
    second = resolver.resolve(
        repo, {"robot": {}}, tmp_path / "other-output", cache, repo_identity="acme/arm"
    )
    assert first.found
    assert second.cache_hit is True
    assert Path(second.model_path).is_file()

    (repo / "notes.txt").write_text("unrelated checkout change")
    unrelated = resolver.resolve(
        repo, {"robot": {}}, tmp_path / "unrelated-output", cache, "acme/arm"
    )
    assert unrelated.cache_hit is True

    (repo / "arm.urdf").write_text(MINIMAL_URDF.replace("two_link", "changed"))
    changed = resolver.resolve(
        repo,
        {"robot": {}},
        tmp_path / "changed-output",
        cache,
        repo_identity="acme/arm",
    )
    assert changed.found
    assert changed.cache_hit is False


def test_stale_cached_model_path_is_a_miss(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "arm.urdf").write_text(MINIMAL_URDF)
    cache = tmp_path / "cache"
    resolver.resolve(repo, {"robot": {}}, tmp_path / "output", cache, "acme/arm")
    entry = next(cache.glob("*.json"))
    payload = json.loads(entry.read_text())
    Path(payload["model_path"]).unlink()

    resolution = resolver.resolve(
        repo, {"robot": {}}, tmp_path / "new-output", cache, "acme/arm"
    )

    assert resolution.found
    assert resolution.cache_hit is False


def test_rejected_explicit_model_is_preserved_in_fallback_provenance(tmp_path) -> None:
    (tmp_path / "bad.xml").write_text("<mujoco><broken></mujoco>")
    (tmp_path / "README.md").write_text("This controller drives franka_emika_panda.\n")

    resolution = resolver.resolve(tmp_path, {"robot": {"model_path": "bad.xml"}})

    assert resolution.found
    assert resolution.approximate is True
    assert "model_path 'bad.xml' was rejected" in resolution.report
    assert "model_path 'bad.xml' was rejected" in (resolution.provenance or "")


def test_scan_joint_limits_reads_real_numbers(tmp_path) -> None:
    (tmp_path / "limits.py").write_text(
        "JOINT_LIMITS = [\n"
        "    (-2.8973, 2.8973),\n"
        "    (-1.7628, 1.7628),\n"
        "    (-2.8973, 2.8973),\n"
        "    (-3.0718, -0.0698),\n"
        "    (-2.8973, 2.8973),\n"
        "    (-0.0175, 3.7525),\n"
        "]\n"
    )
    limits = resolver.scan_joint_limits(tmp_path)
    assert limits
    assert any(
        abs(low + 2.8973) < 1e-6 and abs(high - 2.8973) < 1e-6
        for low, high in limits.values()
    )
    assert len(limits) == 6


def test_scan_joint_limits_reads_min_max_vectors(tmp_path) -> None:
    (tmp_path / "config.py").write_text(
        "Q_MIN = [-2.9, -1.76, -2.9, -3.07, -2.9, -0.02]\n"
        "Q_MAX = [2.9, 1.76, 2.9, -0.07, 2.9, 3.75]\n"
    )
    limits = resolver.scan_joint_limits(tmp_path)
    assert len(limits) == 6
    assert limits["joint0"] == (-2.9, 2.9)


# -- generator -------------------------------------------------------------- #


def test_from_kinematics_produces_a_model_that_holds_itself_up(tmp_path) -> None:
    generated = generator.from_kinematics(
        {"robot_name": "gen6", "dof": 6, "link_lengths": [0.3] * 6}, tmp_path
    )
    assert generated.dof == 6
    assert generated.assumptions, "every guess must be recorded"
    assert 0.0 < generated.confidence < 0.6, "a generated model is low confidence"
    ok, message = generator.validate(Path(generated.model_path))
    assert ok, message


def test_from_urdf_converts_and_validates(tmp_path) -> None:
    urdf = tmp_path / "arm.urdf"
    urdf.write_text(MINIMAL_URDF)
    generated = generator.from_urdf(urdf, tmp_path / "out")
    assert generated.dof == 2
    assert any("inertia" in a.lower() for a in generated.assumptions)
    ok, message = generator.validate(Path(generated.model_path))
    assert ok, message
    compiled = mujoco.MjModel.from_xml_path(generated.model_path)
    assert compiled.nu >= 2, "a model with no actuators cannot run a scenario"


def test_add_gripper_is_idempotent(tmp_path) -> None:
    generated = generator.from_kinematics({"dof": 3}, tmp_path)
    path = Path(generated.model_path)
    generator.add_gripper(path)
    generator.add_gripper(path)
    text = path.read_text()
    assert text.count('name="gripper_base"') == 1
    ok, message = generator.validate(path)
    assert ok, message
    with pytest.raises(ValueError, match="unsupported gripper"):
        generator.add_gripper(path, kind="suction")


def test_validate_rejects_a_broken_model(tmp_path) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<mujoco><worldbody><body/></worldbody>")
    ok, message = generator.validate(broken)
    assert ok is False
    assert "compile" in message
