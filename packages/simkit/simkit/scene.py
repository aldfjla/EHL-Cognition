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

#: Reference cell geometry, metres. Robot-specific geometry is derived below.
TABLE_HEIGHT = 0.4
TABLE_HALF = (0.3, 0.4, 0.02)
TABLE_POS = (0.6, 0.0)
OBJECT_HALF = 0.025
BIN_HALF = (0.09, 0.09, 0.05)
BIN_NOMINAL_POS = (0.55, -0.22)
OBJECT_NOMINAL_POS = (0.55, 0.12)
REFERENCE_REACH = 0.85
WORKING_SURFACE_HEIGHT = 0.0


@dataclass(frozen=True)
class CellGeometry:
    """Task-cell dimensions derived from one robot model."""

    reach_m: float
    scale: float
    table_height: float
    table_half: tuple[float, float, float]
    table_pos: tuple[float, float]
    object_half: float
    object_mass_kg: float
    object_nominal_pos: tuple[float, float]
    bin_half: tuple[float, float, float]
    bin_nominal_pos: tuple[float, float]


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
    geometry: CellGeometry | None = None


class SceneError(RuntimeError):
    """The world could not be built, or a parameter could not be applied."""


def estimate_robot_reach(model: Any) -> float:
    """Estimate radial tool reach by summing offsets along its body chain."""
    tool_site = _gripper_site(model)
    if tool_site is not None:
        body_id = int(model.site_bodyid[tool_site])
    else:
        body_id = _end_effector_body(model)
    if body_id is None:
        return 0.0

    reach = 0.0
    body = body_id
    while body > 0:
        reach += float(np.linalg.norm(np.asarray(model.body_pos[body])[:2]))
        body = int(model.body_parentid[body])
    if tool_site is not None:
        reach += float(np.linalg.norm(np.asarray(model.site_pos[tool_site])[:2]))
    return reach


def derive_cell_geometry(model: Any) -> CellGeometry:
    """Scale the reference task cell to the model's radial tool reach."""
    reach = estimate_robot_reach(model)
    scale = max(reach / REFERENCE_REACH, 0.1)
    return CellGeometry(
        reach_m=reach,
        scale=scale,
        table_height=WORKING_SURFACE_HEIGHT,
        table_half=(
            TABLE_HALF[0] * scale,
            TABLE_HALF[1] * scale,
            max(0.01, TABLE_HALF[2] * scale),
        ),
        table_pos=(TABLE_POS[0] * scale, TABLE_POS[1] * scale),
        object_half=max(0.006, OBJECT_HALF * scale),
        object_mass_kg=max(0.005, 0.3 * scale**3),
        object_nominal_pos=(
            OBJECT_NOMINAL_POS[0] * scale,
            OBJECT_NOMINAL_POS[1] * scale,
        ),
        bin_half=(
            max(0.03, BIN_HALF[0] * scale),
            max(0.03, BIN_HALF[1] * scale),
            max(0.03, BIN_HALF[2] * scale),
        ),
        bin_nominal_pos=(
            BIN_NOMINAL_POS[0] * scale,
            BIN_NOMINAL_POS[1] * scale,
        ),
    )


def _reference_cell_geometry() -> CellGeometry:
    """Return the unscaled cell for compatibility with manually-built Scenes."""
    return CellGeometry(
        reach_m=REFERENCE_REACH,
        scale=1.0,
        table_height=TABLE_HEIGHT,
        table_half=TABLE_HALF,
        table_pos=TABLE_POS,
        object_half=OBJECT_HALF,
        object_mass_kg=0.3,
        object_nominal_pos=OBJECT_NOMINAL_POS,
        bin_half=BIN_HALF,
        bin_nominal_pos=BIN_NOMINAL_POS,
    )


def build(spec: SceneSpec) -> Scene:
    """Compile robot + task world into a ready-to-step Scene."""
    robot_path = Path(spec.robot_model_path).resolve()
    if not robot_path.is_file():
        raise SceneError(f"robot model not found: {robot_path}")

    robot_model = mujoco.MjModel.from_xml_path(str(robot_path))
    geometry = derive_cell_geometry(robot_model)
    xml = task_world_xml(robot_path, spec, geometry)
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
    scene = Scene(
        model=model,
        data=data,
        spec=spec,
        handles=_resolve_handles(model),
        geometry=geometry,
    )
    apply_params(scene, spec.params)
    reset(scene, int(spec.params.get("seed", 0) or 0))
    return scene


def task_world_xml(
    robot_path: Path, spec: SceneSpec, geometry: CellGeometry | None = None
) -> str:
    """The task MJCF that includes the robot model without modifying it."""
    if geometry is None:
        geometry = derive_cell_geometry(mujoco.MjModel.from_xml_path(str(robot_path)))
    task = spec.task_name or "pick_and_place"
    mjcf = ET.Element("mujoco", model=f"robotci_{task}")
    include = ET.SubElement(mjcf, "include")
    include.set("file", robot_path.name)

    statistic = ET.SubElement(mjcf, "statistic")
    statistic.set(
        "center",
        f"{geometry.table_pos[0]:.6f} 0 {geometry.table_height:.6f}",
    )
    statistic.set("extent", f"{max(1.0, 1.4 * geometry.scale):.6f}")

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
        f"{geometry.table_pos[0]:.6f} {geometry.table_pos[1]:.6f} "
        f"{geometry.table_height - geometry.table_half[2]:.6f}",
    )
    table_geom = ET.SubElement(table, "geom")
    table_geom.set("name", "robotci_table_top")
    table_geom.set("type", "box")
    table_geom.set("size", " ".join(f"{v:.6f}" for v in geometry.table_half))
    table_geom.set("rgba", "0.55 0.45 0.35 1")
    table_geom.set("friction", "1 0.005 0.0001")

    obj = ET.SubElement(worldbody, "body", name="robotci_object")
    obj.set(
        "pos",
        f"{geometry.object_nominal_pos[0]:.6f} {geometry.object_nominal_pos[1]:.6f} "
        f"{geometry.table_height + geometry.object_half:.6f}",
    )
    ET.SubElement(obj, "freejoint").set("name", "robotci_object_free")
    obj_geom = ET.SubElement(obj, "geom")
    obj_geom.set("name", "robotci_object_geom")
    obj_geom.set("type", "box")
    obj_geom.set("size", " ".join([f"{geometry.object_half:.6f}"] * 3))
    obj_geom.set("mass", f"{geometry.object_mass_kg:.6f}")
    obj_geom.set("rgba", "0.85 0.35 0.25 1")
    obj_geom.set("friction", "1 0.02 0.001")
    ET.SubElement(obj, "site", name="robotci_object_site").set("size", "0.005")

    bin_body = ET.SubElement(worldbody, "body", name="robotci_bin")
    bin_body.set(
        "pos",
        f"{geometry.bin_nominal_pos[0]:.6f} {geometry.bin_nominal_pos[1]:.6f} "
        f"{geometry.table_height:.6f}",
    )
    for name, pos, size in _bin_walls(geometry.bin_half):
        wall = ET.SubElement(bin_body, "geom")
        wall.set("name", name)
        wall.set("type", "box")
        wall.set("pos", pos)
        wall.set("size", size)
        wall.set("rgba", "0.3 0.5 0.75 1")
    ET.SubElement(bin_body, "site", name="robotci_bin_site").set(
        "pos", f"0 0 {geometry.bin_half[2]:.4f}"
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
            nominal = (
                scene.geometry.bin_nominal_pos if scene.geometry else BIN_NOMINAL_POS
            )
            model.body_pos[body, axis] = nominal[axis] + float(value)
        elif key.startswith("object_position."):
            # The object is free: its offset belongs to the reset pose, which
            # reset() reads back out of the spec.
            continue
        elif key == "table_height_m":
            body = scene.handles.get("table")
            if body is None:
                unknown.append(key)
                continue
            table_half = scene.geometry.table_half if scene.geometry else TABLE_HALF
            model.body_pos[body, 2] = float(value) - table_half[2]

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
    geometry = scene.geometry or _reference_cell_geometry()
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    jitter = rng.normal(0.0, 1e-4, size=3)

    joint = scene.handles.get("object_free_joint")
    if joint is not None:
        adr = int(model.jnt_qposadr[joint])
        table_h = float(params.get("table_height_m", geometry.table_height))
        data.qpos[adr + 0] = (
            geometry.object_nominal_pos[0]
            + float(params.get("object_position.x", 0.0))
            + jitter[0]
        )
        data.qpos[adr + 1] = (
            geometry.object_nominal_pos[1]
            + float(params.get("object_position.y", 0.0))
            + jitter[1]
        )
        data.qpos[adr + 2] = (
            table_h + geometry.object_half + float(params.get("object_position.z", 0.0))
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


def _bin_walls(
    bin_half: tuple[float, float, float] = BIN_HALF,
) -> list[tuple[str, str, str]]:
    hx, hy, hz = bin_half
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
    """Find a plausible tool site, preferring the deepest attached site."""
    exact_names = (
        "attachment_site",
        "gripper",
        "gripper_frame",
        "gripperframe",
        "grip_site",
        "gripframe",
        "pinch",
        "pinch_site",
        "ee_site",
        "eef",
        "tcp",
        "tcp_site",
        "tool",
        "tool_site",
        "tool0",
    )
    exact_rank = {name: index for index, name in enumerate(exact_names)}
    tokens = ("grip", "tcp", "tool", "pinch", "ee", "end_effector")

    def depth(body_id: int) -> int:
        value = 0
        body = body_id
        while body > 0:
            value += 1
            body = int(model.body_parentid[body])
        return value

    candidates: list[tuple[tuple[int, int, int], int]] = []
    for sid in range(model.nsite):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SITE, sid) or ""
        if name.startswith("robotci_"):
            continue
        lowered = name.lower()
        normalized = lowered.replace("-", "_")
        body_depth = depth(int(model.site_bodyid[sid]))
        if normalized in exact_rank:
            score = (body_depth, 2, -exact_rank[normalized])
        elif any(token in lowered for token in tokens):
            score = (body_depth, 1, 0)
        else:
            continue
        candidates.append((score, sid))
    if not candidates:
        return None
    return max(candidates)[1]
