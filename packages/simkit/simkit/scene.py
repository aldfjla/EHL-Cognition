"""Assemble a complete simulated world: robot + task environment + sensors.

Responsibility
--------------
Take a resolved robot model and a scenario's parameters and produce a loaded
MuJoCo model/data pair ready to step.

Inputs:  path to the robot MJCF, a task definition from ``robotci.yaml``, and
         the concrete parameters of one scenario.
Outputs: ``(mujoco.MjModel, mujoco.MjData)`` plus a description of what is in
         the scene, for the recorder's overlay and the report.

Composition approach
--------------------
The robot MJCF is included, never edited — Menagerie models are curated and
rewriting them is how physical fidelity silently degrades. Task geometry
(table, target object, bin, obstacles) is generated as a separate MJCF that
``<include>``s the robot, and per-scenario parameters are applied to the
compiled model afterwards where MuJoCo allows it (masses, frictions, positions)
rather than by string-templating XML.
"""

from __future__ import annotations

import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mujoco
import numpy as np

#: Parameters that describe the *world* and are applied to the compiled model.
MODEL_PARAMS = frozenset(
    {
        "object_mass_kg",
        "friction",
        "object_position.x",
        "object_position.y",
        "object_position.z",
        "bin_position.x",
        "bin_position.y",
        "table_height_m",
        "gravity_z",
    }
)
#: Parameters the runner consumes while stepping; they have no model field.
RUNTIME_PARAMS = frozenset(
    {
        "sensor_noise_std",
        "latency_steps",
        "control_dropout",
    }
)

#: Nominal geometry of the generated pick-and-place cell, metres.
TABLE_HEIGHT = 0.4
TABLE_HALF = (0.3, 0.4, 0.02)
#: Table centre in the robot's frame, far enough out to clear the base.
TABLE_POS = (0.6, 0.0)
OBJECT_HALF = 0.025
BIN_HALF = (0.09, 0.09, 0.05)
BIN_NOMINAL_POS = (0.55, -0.22)
OBJECT_NOMINAL_POS = (0.55, 0.12)


@dataclass
class SceneSpec:
    """Everything needed to build one world."""

    robot_model_path: str
    task_name: str
    params: dict[str, Any] = field(default_factory=dict)
    include_visuals: bool = True


@dataclass
class Scene:
    """A compiled world plus the handles the runner and scorer need."""

    model: Any  # mujoco.MjModel
    data: Any  # mujoco.MjData
    spec: SceneSpec
    #: Named body/site/geom ids resolved once at build time, e.g.
    #: ``{"object": 12, "bin": 19, "gripper_site": 4}``.
    handles: dict[str, int] = field(default_factory=dict)


class SceneError(RuntimeError):
    """The world could not be built, or a parameter could not be applied."""


def build(spec: SceneSpec) -> Scene:
    """Compile robot + task world into a ready-to-step Scene."""
    robot_path = Path(spec.robot_model_path).resolve()
    if not robot_path.is_file():
        raise SceneError(f"robot model not found: {robot_path}")

    xml = task_world_xml(robot_path, spec)
    # The world file must sit beside the robot XML: Menagerie models declare
    # `meshdir="assets"` relative to the *main* file, so a world compiled from
    # anywhere else cannot find their meshes.
    staged = robot_path.parent / f".robotci_world_{os.getpid()}_{uuid.uuid4().hex}.xml"
    try:
        staged.write_text(xml)
        model = mujoco.MjModel.from_xml_path(str(staged))
    except ValueError as exc:
        raise SceneError(
            f"world for {robot_path.name} does not compile: {exc}"
        ) from exc
    except OSError as exc:
        raise SceneError(f"cannot stage world next to {robot_path}: {exc}") from exc
    finally:
        staged.unlink(missing_ok=True)

    data = mujoco.MjData(model)
    scene = Scene(model=model, data=data, spec=spec, handles=_resolve_handles(model))
    apply_params(scene, spec.params)
    reset(scene, int(spec.params.get("seed", 0) or 0))
    return scene


def task_world_xml(robot_path: Path, spec: SceneSpec) -> str:
    """The task MJCF that includes the robot model without modifying it."""
    task = spec.task_name or "pick_and_place"
    mjcf = ET.Element("mujoco", model=f"robotci_{task}")
    include = ET.SubElement(mjcf, "include")
    include.set("file", robot_path.name)

    statistic = ET.SubElement(mjcf, "statistic")
    statistic.set("center", f"{TABLE_POS[0]:.2f} 0 {TABLE_HEIGHT:.2f}")
    statistic.set("extent", "1.4")

    if spec.include_visuals:
        visual = ET.SubElement(mjcf, "visual")
        headlight = ET.SubElement(visual, "headlight")
        headlight.set("diffuse", "0.6 0.6 0.6")
        headlight.set("ambient", "0.35 0.35 0.35")
        global_ = ET.SubElement(visual, "global")
        global_.set("azimuth", "150")
        global_.set("elevation", "-25")

    worldbody = ET.SubElement(mjcf, "worldbody")
    light = ET.SubElement(worldbody, "light")
    light.set("pos", "0 0 2.5")
    light.set("dir", "0 0 -1")
    light.set("directional", "true")

    floor = ET.SubElement(worldbody, "geom")
    floor.set("name", "robotci_floor")
    floor.set("type", "plane")
    floor.set("size", "3 3 0.05")
    floor.set("rgba", "0.25 0.26 0.3 1")

    table = ET.SubElement(worldbody, "body", name="robotci_table")
    table.set(
        "pos",
        f"{TABLE_POS[0]:.4f} {TABLE_POS[1]:.4f} {TABLE_HEIGHT - TABLE_HALF[2]:.4f}",
    )
    table_geom = ET.SubElement(table, "geom")
    table_geom.set("name", "robotci_table_top")
    table_geom.set("type", "box")
    table_geom.set("size", " ".join(f"{v:.4f}" for v in TABLE_HALF))
    table_geom.set("rgba", "0.55 0.45 0.35 1")
    table_geom.set("friction", "1 0.005 0.0001")

    obj = ET.SubElement(worldbody, "body", name="robotci_object")
    obj.set(
        "pos",
        f"{OBJECT_NOMINAL_POS[0]:.4f} {OBJECT_NOMINAL_POS[1]:.4f} "
        f"{TABLE_HEIGHT + OBJECT_HALF:.4f}",
    )
    ET.SubElement(obj, "freejoint").set("name", "robotci_object_free")
    obj_geom = ET.SubElement(obj, "geom")
    obj_geom.set("name", "robotci_object_geom")
    obj_geom.set("type", "box")
    obj_geom.set("size", " ".join([f"{OBJECT_HALF:.4f}"] * 3))
    obj_geom.set("mass", "0.3")
    obj_geom.set("rgba", "0.85 0.35 0.25 1")
    obj_geom.set("friction", "1 0.02 0.001")
    ET.SubElement(obj, "site", name="robotci_object_site").set("size", "0.005")

    bin_body = ET.SubElement(worldbody, "body", name="robotci_bin")
    bin_body.set(
        "pos",
        f"{BIN_NOMINAL_POS[0]:.4f} {BIN_NOMINAL_POS[1]:.4f} {TABLE_HEIGHT:.4f}",
    )
    for name, pos, size in _bin_walls():
        wall = ET.SubElement(bin_body, "geom")
        wall.set("name", name)
        wall.set("type", "box")
        wall.set("pos", pos)
        wall.set("size", size)
        wall.set("rgba", "0.3 0.5 0.75 1")
    ET.SubElement(bin_body, "site", name="robotci_bin_site").set(
        "pos", f"0 0 {BIN_HALF[2]:.4f}"
    )
    ET.SubElement(mjcf, "sensor")
    ET.indent(mjcf, space="  ")
    return ET.tostring(mjcf, encoding="unicode")


def apply_params(scene: Scene, params: dict[str, Any]) -> None:
    """Apply scenario parameters to an already-compiled model.

    Mutates masses, frictions, initial positions and noise settings in place.
    Anything that cannot be applied post-compile must be reported, not silently
    ignored — a scenario that did not actually randomize is a false pass.
    """
    model = scene.model
    unknown: list[str] = []
    for key, value in (params or {}).items():
        if key in {"seed", "index", "label"}:
            continue
        if key in RUNTIME_PARAMS:
            continue
        if key not in MODEL_PARAMS:
            unknown.append(key)
            continue

        if key == "object_mass_kg":
            body = scene.handles.get("object")
            if body is None:
                unknown.append(key)
                continue
            old = float(model.body_mass[body])
            new = float(value)
            if new <= 0:
                raise SceneError(f"object_mass_kg must be positive, got {new}")
            model.body_mass[body] = new
            # Inertia of a uniform body scales linearly with mass.
            model.body_inertia[body] *= new / old if old > 0 else 1.0
        elif key == "friction":
            for handle in ("object_geom", "table_geom"):
                geom = scene.handles.get(handle)
                if geom is not None:
                    model.geom_friction[geom, 0] = float(value)
        elif key == "gravity_z":
            model.opt.gravity[2] = float(value)
        elif key.startswith("bin_position."):
            # The bin is static geometry, so its offset is a model edit.
            body = scene.handles.get("bin")
            axis = {"x": 0, "y": 1}.get(key.rsplit(".", 1)[-1])
            if body is None or axis is None:
                unknown.append(key)
                continue
            model.body_pos[body, axis] = BIN_NOMINAL_POS[axis] + float(value)
        elif key.startswith("object_position."):
            # The object is free: its offset belongs to the reset pose, which
            # reset() reads back out of the spec.
            continue
        elif key == "table_height_m":
            body = scene.handles.get("table")
            if body is None:
                unknown.append(key)
                continue
            model.body_pos[body, 2] = float(value) - TABLE_HALF[2]

    if unknown:
        raise SceneError(
            "scenario parameters cannot be applied to the compiled model: "
            + ", ".join(sorted(unknown))
            + " (a scenario that silently does not randomize is a false pass)"
        )
    scene.spec.params = dict(params or {})


def reset(scene: Scene, seed: int) -> None:
    """Reset to the deterministic initial state for ``seed``."""
    model, data = scene.model, scene.data
    mujoco.mj_resetData(model, data)

    if model.nkey > 0:
        # Menagerie models ship a `home` keyframe: the pose the vendor considers
        # a sane starting configuration.
        key = 0
        for i in range(model.nkey):
            if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_KEY, i) == "home":
                key = i
                break
        mujoco.mj_resetDataKeyframe(model, data, key)

    params = scene.spec.params or {}
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    jitter = rng.normal(0.0, 1e-4, size=3)

    joint = scene.handles.get("object_free_joint")
    if joint is not None:
        adr = int(model.jnt_qposadr[joint])
        table_h = float(params.get("table_height_m", TABLE_HEIGHT))
        data.qpos[adr + 0] = (
            OBJECT_NOMINAL_POS[0]
            + float(params.get("object_position.x", 0.0))
            + jitter[0]
        )
        data.qpos[adr + 1] = (
            OBJECT_NOMINAL_POS[1]
            + float(params.get("object_position.y", 0.0))
            + jitter[1]
        )
        data.qpos[adr + 2] = (
            table_h + OBJECT_HALF + float(params.get("object_position.z", 0.0))
        )
        data.qpos[adr + 3 : adr + 7] = (1.0, 0.0, 0.0, 0.0)

    if model.nu:
        # Hold whatever the reset pose is: a harness that never writes ctrl
        # should leave the arm where it started, not command it to zero and
        # slam it into the table.
        _hold_reset_pose(scene)
    mujoco.mj_forward(model, data)


def _hold_reset_pose(scene: Scene) -> None:
    """Seed ``data.ctrl`` so the reset pose is the commanded pose."""
    model, data = scene.model, scene.data
    data.ctrl[:] = 0.0
    for actuator in range(model.nu):
        trnid = int(model.actuator_trnid[actuator, 0])
        trntype = int(model.actuator_trntype[actuator])
        if trntype != int(mujoco.mjtTrn.mjTRN_JOINT):
            continue
        adr = int(model.jnt_qposadr[trnid])
        value = float(data.qpos[adr])
        low, high = model.actuator_ctrlrange[actuator]
        if bool(model.actuator_ctrllimited[actuator]) and high > low:
            value = min(max(value, float(low)), float(high))
        data.ctrl[actuator] = value


def describe(scene: Scene) -> dict[str, Any]:
    """A small summary of the world, for video overlays and the report."""
    model = scene.model
    return {
        "robot_model": Path(scene.spec.robot_model_path).name,
        "task": scene.spec.task_name,
        "dof": int(model.nv),
        "actuators": int(model.nu),
        "bodies": int(model.nbody),
        "timestep": float(model.opt.timestep),
        "params": dict(scene.spec.params or {}),
    }


def robot_joint_ids(scene: Scene) -> list[int]:
    """Actuated robot joints, excluding the task's own free joints."""
    model = scene.model
    ids: list[int] = []
    for jid in range(model.njnt):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid) or ""
        if name.startswith("robotci_"):
            continue
        if int(model.jnt_type[jid]) in {
            int(mujoco.mjtJoint.mjJNT_FREE),
            int(mujoco.mjtJoint.mjJNT_BALL),
        }:
            continue
        ids.append(jid)
    return ids


def _bin_walls() -> list[tuple[str, str, str]]:
    hx, hy, hz = BIN_HALF
    thickness = 0.006
    return [
        ("robotci_bin_floor", f"0 0 {thickness:.4f}", f"{hx:.4f} {hy:.4f} {thickness}"),
        (
            "robotci_bin_wall_x_pos",
            f"{hx:.4f} 0 {hz:.4f}",
            f"{thickness} {hy:.4f} {hz:.4f}",
        ),
        (
            "robotci_bin_wall_x_neg",
            f"{-hx:.4f} 0 {hz:.4f}",
            f"{thickness} {hy:.4f} {hz:.4f}",
        ),
        (
            "robotci_bin_wall_y_pos",
            f"0 {hy:.4f} {hz:.4f}",
            f"{hx:.4f} {thickness} {hz:.4f}",
        ),
        (
            "robotci_bin_wall_y_neg",
            f"0 {-hy:.4f} {hz:.4f}",
            f"{hx:.4f} {thickness} {hz:.4f}",
        ),
    ]


def _resolve_handles(model: Any) -> dict[str, int]:
    """Resolve the named entities the runner and scorer look up by name."""

    def body(name: str) -> int | None:
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        return bid if bid >= 0 else None

    def geom(name: str) -> int | None:
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        return gid if gid >= 0 else None

    def site(name: str) -> int | None:
        sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, name)
        return sid if sid >= 0 else None

    handles: dict[str, int] = {}
    for key, value in (
        ("object", body("robotci_object")),
        ("bin", body("robotci_bin")),
        ("table", body("robotci_table")),
        ("object_geom", geom("robotci_object_geom")),
        ("table_geom", geom("robotci_table_top")),
        ("floor_geom", geom("robotci_floor")),
        ("object_site", site("robotci_object_site")),
        ("bin_site", site("robotci_bin_site")),
        ("gripper_site", _gripper_site(model)),
        ("ee_body", _end_effector_body(model)),
        (
            "object_free_joint",
            _joint(model, "robotci_object_free"),
        ),
    ):
        if value is not None:
            handles[key] = int(value)
    return handles


def _joint(model: Any, name: str) -> int | None:
    jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
    return jid if jid >= 0 else None


def _end_effector_body(model: Any) -> int | None:
    """The tip of the robot's longest body chain — used when no site is named."""
    best: int | None = None
    best_depth = -1
    for bid in range(1, model.nbody):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, bid) or ""
        if name.startswith("robotci_"):
            continue
        depth = 0
        parent = int(model.body_parentid[bid])
        while parent > 0 and depth < 64:
            depth += 1
            parent = int(model.body_parentid[parent])
        if depth > best_depth:
            best, best_depth = bid, depth
    return best


def _gripper_site(model: Any) -> int | None:
    """Whatever site the vendor model uses as the tool attachment point."""
    for candidate in (
        "attachment_site",
        "gripper",
        "grip_site",
        "pinch",
        "ee_site",
        "tcp",
    ):
        sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, candidate)
        if sid >= 0:
            return sid
    for sid in range(model.nsite):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, sid) or ""
        if not name.startswith("robotci_"):
            return sid
    return None
