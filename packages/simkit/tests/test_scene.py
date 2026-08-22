"""Scene composition: parameters must actually reach the compiled model."""

from __future__ import annotations

import mujoco
import numpy as np
import pytest
from simkit import scene as scene_mod


def make(toy_arm, **params):
    spec = scene_mod.SceneSpec(
        robot_model_path=str(toy_arm), task_name="pick_and_place", params=params
    )
    return scene_mod.build(spec)


def test_build_composes_robot_and_task_geometry(toy_arm) -> None:
    scene = make(toy_arm)
    assert scene.model.nu >= 2, "the robot's actuators must survive composition"
    for handle in ("object", "bin", "table"):
        assert handle in scene.handles, scene.handles
    described = scene_mod.describe(scene)
    assert described["dof"] > 6  # arm joints plus the object's free joint
    assert scene_mod.robot_joint_ids(scene), "robot joints must be identifiable"


def test_curated_robot_xml_is_not_modified(toy_arm) -> None:
    before = toy_arm.read_bytes()
    make(toy_arm)
    assert toy_arm.read_bytes() == before


def test_missing_robot_model_is_reported(tmp_path) -> None:
    with pytest.raises(scene_mod.SceneError, match="not found"):
        make(tmp_path / "nope.xml")


def test_params_reach_the_compiled_model(toy_arm) -> None:
    scene = make(toy_arm)
    scene_mod.apply_params(scene, {"object_mass_kg": 1.7, "friction": 0.3})
    body = scene.handles["object"]
    assert scene.model.body_mass[body] == pytest.approx(1.7)
    geom = scene.model.body_geomadr[body]
    assert scene.model.geom_friction[geom, 0] == pytest.approx(0.3)


def test_gravity_and_table_height_are_applied(toy_arm) -> None:
    scene = make(toy_arm)
    scene_mod.apply_params(scene, {"gravity_z": -3.7, "bin_position.y": -0.1})
    assert scene.model.opt.gravity[2] == pytest.approx(-3.7)
    # bin_position is an offset from the nominal cell layout.
    assert scene.model.body_pos[scene.handles["bin"], 1] == pytest.approx(
        scene.geometry.bin_nominal_pos[1] - 0.1
    )
    scene_mod.apply_params(scene, {"table_height_m": 0.5})
    assert scene.model.body_pos[scene.handles["table"], 2] == pytest.approx(
        0.5 - scene.geometry.table_half[2]
    )


def test_unknown_param_is_not_silently_ignored(toy_arm) -> None:
    scene = make(toy_arm)
    with pytest.raises(scene_mod.SceneError, match="bogus_param"):
        scene_mod.apply_params(scene, {"bogus_param": 1.0})


def test_runtime_params_are_left_for_the_runner(toy_arm) -> None:
    scene = make(toy_arm)
    scene_mod.apply_params(scene, {"sensor_noise_std": 0.01, "latency_steps": 2})


def test_reset_is_deterministic_per_seed(toy_arm) -> None:
    scene = make(toy_arm)
    scene_mod.reset(scene, 99)
    first = np.array(scene.data.qpos, copy=True)
    scene_mod.reset(scene, 99)
    assert np.array_equal(scene.data.qpos, first)
    scene_mod.reset(scene, 100)
    assert not np.array_equal(scene.data.qpos, first), "the seed must jitter the world"


def test_reset_holds_the_home_pose_not_zero(toy_arm) -> None:
    """A harness that never writes ctrl must not slam the arm to zero."""
    scene = make(toy_arm)
    scene_mod.reset(scene, 7)
    joints = scene_mod.robot_joint_ids(scene)
    poses = [scene.data.qpos[scene.model.jnt_qposadr[j]] for j in joints]
    assert any(abs(float(p)) > 0.1 for p in poses)
    assert np.any(scene.data.ctrl != 0.0)


def test_robot_scale_changes_reach_and_cell_geometry(menagerie_dir) -> None:
    so_path = menagerie_dir / "robotstudio_so101" / "so101.xml"
    panda_path = menagerie_dir / "franka_emika_panda" / "panda.xml"
    so_model = mujoco.MjModel.from_xml_path(str(so_path))
    panda_model = mujoco.MjModel.from_xml_path(str(panda_path))

    so_geometry = scene_mod.derive_cell_geometry(so_model)
    panda_geometry = scene_mod.derive_cell_geometry(panda_model)

    assert so_geometry.reach_m < panda_geometry.reach_m
    assert so_geometry.scale < panda_geometry.scale
    for geometry in (so_geometry, panda_geometry):
        object_radius = np.linalg.norm(geometry.object_nominal_pos)
        assert object_radius < geometry.reach_m


def test_scaled_home_poses_have_no_contacts(menagerie_dir) -> None:
    for model_name in ("robotstudio_so101", "franka_emika_panda"):
        model_path = menagerie_dir / model_name
        xml_name = "so101.xml" if model_name == "robotstudio_so101" else "panda.xml"
        scene = make(model_path / xml_name)
        mujoco.mj_forward(scene.model, scene.data)
        for index in range(scene.data.ncon):
            contact = scene.data.contact[index]
            names = [
                mujoco.mj_id2name(
                    scene.model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom1)
                ),
                mujoco.mj_id2name(
                    scene.model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom2)
                ),
            ]
            robot_contact = any(
                name and not name.startswith("robotci_") for name in names
            )
            task_contact = any(name and name.startswith("robotci_") for name in names)
            assert not (robot_contact and task_contact), (model_name, names)


def test_gripper_site_prefers_so101_gripperframe(menagerie_dir) -> None:
    model_path = menagerie_dir / "robotstudio_so101" / "so101.xml"
    model = mujoco.MjModel.from_xml_path(str(model_path))
    site_id = scene_mod._gripper_site(model)
    assert site_id is not None
    assert mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, site_id) == (
        "gripperframe"
    )


def test_gripper_site_does_not_use_unrelated_base_site() -> None:
    model = mujoco.MjModel.from_xml_string(
        """
        <mujoco>
          <worldbody>
            <body name="base">
              <site name="baseframe"/>
            </body>
          </worldbody>
        </mujoco>
        """
    )
    assert scene_mod._gripper_site(model) is None
