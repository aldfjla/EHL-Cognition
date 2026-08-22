"""Fallback: synthesize an MJCF model from a robot's kinematics.

Responsibility
--------------
When the library has nothing, build a physically plausible model from whatever
the repo describes — a URDF, a DH parameter table, or joint limits alone.

Inputs:  a kinematics dict (from ``resolver.parse_urdf`` or the Modeler agent).
Outputs: an MJCF file path, plus a list of assumptions made.

Honesty requirement
-------------------
A generated model is a **guess about physics**, and every downstream conclusion
inherits that guess. So:

* every inferred quantity (mass, inertia, damping, friction) is recorded in
  ``assumptions`` and surfaced as a ``constraint`` finding on the blackboard;
* ``confidence`` is reported low, and the report says the model was generated.

A failure found against a generated model is a *lead*, not a verdict. Saying so
plainly is more valuable than a system that quietly presents guesses as facts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

#: Aluminium-ish effective density for a capsule link, kg/m^3. Real arms are
#: hollow, so this is deliberately well under solid aluminium (2700).
LINK_DENSITY = 900.0
#: Radius of a generated capsule link, metres.
LINK_RADIUS = 0.045
#: Fallback length when the source says nothing about how long a link is.
DEFAULT_LINK_LENGTH = 0.25


@dataclass
class GeneratedModel:
    """A synthesized model and the guesses behind it."""

    model_path: str
    dof: int
    confidence: float = 0.3
    assumptions: list[str] = field(default_factory=list)


def from_urdf(urdf_path: Path, out_dir: Path) -> GeneratedModel:
    """Convert a URDF to MJCF. The best fallback — real link geometry.

    MuJoCo can compile URDF directly, but the result needs actuators, sensible
    damping and collision filtering added before it simulates usefully.
    """
    urdf_path = Path(urdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assumptions: list[str] = []

    spec = ET.parse(urdf_path)
    root = spec.getroot()
    # MuJoCo's URDF importer needs a compiler directive to infer the inertias
    # that most hand-written URDFs omit, and meshes may not be resolvable at
    # all, so strip visual meshes and keep collision primitives.
    extension = ET.Element("mujoco")
    compiler = ET.SubElement(extension, "compiler")
    compiler.set("balanceinertia", "true")
    compiler.set("discardvisual", "true")
    compiler.set("fusestatic", "false")
    compiler.set("strippath", "true")
    root.insert(0, extension)
    assumptions.append(
        "URDF inertias were balanced by the MuJoCo compiler "
        "(balanceinertia=true); any missing or inconsistent inertia tensor was "
        "replaced with a plausible one."
    )
    assumptions.append("Visual meshes were discarded; collision geometry only.")

    massless = _fill_missing_inertials(root)
    if massless:
        assumptions.append(
            f"Links {', '.join(massless)} carried no inertial element; each was "
            "given a 1 kg mass with a 0.01 kgm^2 diagonal inertia so the model "
            "compiles."
        )

    staged = out_dir / f"{urdf_path.stem}_staged.urdf"
    model = None
    last_error = ""
    # Mesh references in a customer URDF are relative to anything at all, so
    # try the plausible asset roots rather than giving up on the first miss.
    for meshdir in _meshdir_candidates(urdf_path):
        compiler.set("meshdir", meshdir)
        staged.write_bytes(ET.tostring(root))
        try:
            model = mujoco.MjModel.from_xml_path(str(staged))
        except ValueError as exc:
            last_error = str(exc)
            continue
        if meshdir != ".":
            assumptions.append(f"Mesh assets were resolved relative to {meshdir!r}.")
        break
    if model is None:
        raise ValueError(f"MuJoCo could not compile {urdf_path}: {last_error}")

    xml_out = out_dir / f"{urdf_path.stem}_generated.xml"
    mujoco.mj_saveLastXML(str(xml_out), model)

    dof = _inject_actuators(xml_out, assumptions)
    ok, message = validate(xml_out)
    if not ok:
        assumptions.append(f"Validation warning: {message}")
    return GeneratedModel(
        model_path=str(xml_out),
        dof=dof,
        confidence=0.45 if ok else 0.2,
        assumptions=assumptions,
    )


def from_kinematics(spec: dict, out_dir: Path) -> GeneratedModel:
    """Build a serial-chain MJCF from link lengths and joint axes/limits."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assumptions: list[str] = []

    name = str(spec.get("robot_name") or "generated_robot")
    joints = list(spec.get("joints") or [])
    if not joints:
        dof = int(spec.get("dof") or 6)
        joints = [
            {"name": f"joint{i}", "type": "revolute", "axis": [0.0, 1.0, 0.0]}
            for i in range(dof)
        ]
        assumptions.append(
            f"No joint description was available; assumed a {dof}-DOF serial "
            "chain of revolute joints alternating about the y and z axes."
        )
    lengths = list(spec.get("link_lengths") or [])

    mjcf = ET.Element("mujoco", model=name)
    compiler = ET.SubElement(mjcf, "compiler")
    compiler.set("angle", "radian")
    compiler.set("autolimits", "true")
    option = ET.SubElement(mjcf, "option")
    option.set("integrator", "implicitfast")
    option.set("timestep", "0.002")

    default = ET.SubElement(mjcf, "default")
    joint_default = ET.SubElement(default, "joint")
    joint_default.set("damping", "1.0")
    joint_default.set("armature", "0.1")
    geom_default = ET.SubElement(default, "geom")
    geom_default.set("friction", "1 0.005 0.0001")
    geom_default.set("rgba", "0.7 0.7 0.75 1")
    assumptions.append(
        "Joint damping 1.0 Nms/rad and armature 0.1 kgm^2 were assumed; the "
        "real robot's drivetrain friction and rotor inertia are unknown."
    )
    assumptions.append(
        f"Links are capsules of radius {LINK_RADIUS} m with an effective "
        f"density of {LINK_DENSITY} kg/m^3, so masses and inertias are inferred "
        "from length alone."
    )

    worldbody = ET.SubElement(mjcf, "worldbody")
    light = ET.SubElement(worldbody, "light")
    light.set("pos", "0 0 2")
    light.set("dir", "0 0 -1")
    ground = ET.SubElement(worldbody, "geom")
    ground.set("name", "floor")
    ground.set("type", "plane")
    ground.set("size", "2 2 0.05")
    ground.set("rgba", "0.3 0.3 0.35 1")

    parent = ET.SubElement(worldbody, "body", name="base")
    parent.set("pos", "0 0 0.05")
    base_geom = ET.SubElement(parent, "geom")
    base_geom.set("name", "base_geom")
    base_geom.set("type", "cylinder")
    base_geom.set("size", "0.08 0.05")
    base_geom.set("mass", "5.0")

    joint_names: list[str] = []
    for i, joint in enumerate(joints):
        length = float(lengths[i]) if i < len(lengths) and lengths[i] else 0.0
        if length < 1e-3:
            length = DEFAULT_LINK_LENGTH
        body = ET.SubElement(parent, "body", name=f"link{i + 1}")
        body.set("pos", f"0 0 {0.05 if i == 0 else length:.4f}")
        jname = str(joint.get("name") or f"joint{i}")
        joint_names.append(jname)
        element = ET.SubElement(body, "joint", name=jname)
        jtype = str(joint.get("type") or "revolute")
        element.set("type", "slide" if jtype in {"prismatic", "slide"} else "hinge")
        axis = joint.get("axis") or ([0, 0, 1] if i % 2 == 0 else [0, 1, 0])
        element.set("axis", " ".join(f"{float(v):.4f}" for v in axis[:3]))
        lower = float(joint.get("lower", -2.9))
        upper = float(joint.get("upper", 2.9))
        element.set("range", f"{lower:.4f} {upper:.4f}")
        if "lower" not in joint:
            assumptions.append(
                f"Joint {jname} limits were unknown; assumed +/-2.9 rad."
            )
        geom = ET.SubElement(body, "geom")
        geom.set("name", f"link{i + 1}_geom")
        geom.set("type", "capsule")
        geom.set("fromto", f"0 0 0 0 0 {length:.4f}")
        geom.set("size", f"{LINK_RADIUS}")
        geom.set("density", f"{LINK_DENSITY}")
        parent = body

    site = ET.SubElement(parent, "site", name="attachment_site")
    site.set("pos", "0 0 0.02")
    site.set("size", "0.01")

    actuator = ET.SubElement(mjcf, "actuator")
    for jname in joint_names:
        position = ET.SubElement(actuator, "position", name=f"{jname}_act")
        position.set("joint", jname)
        position.set("kp", "300")
        position.set("kv", "30")
        position.set("ctrlrange", "-3.14159 3.14159")
    assumptions.append(
        "Actuators are position servos with kp=300, kv=30 — sized to hold the "
        "arm against gravity, not to match the real controller's gains."
    )

    out_path = out_dir / f"{name}_generated.xml"
    _write_xml(mjcf, out_path)

    ok, message = validate(out_path)
    if not ok:
        assumptions.append(f"Validation warning: {message}")
    return GeneratedModel(
        model_path=str(out_path),
        dof=len(joint_names),
        confidence=0.3 if ok else 0.15,
        assumptions=assumptions,
    )


def add_gripper(model_path: Path, kind: str = "parallel_jaw") -> None:
    """Attach a generic gripper to the end effector."""
    if kind != "parallel_jaw":
        raise ValueError(f"unsupported gripper kind: {kind!r}")
    model_path = Path(model_path)
    tree = ET.parse(model_path)
    root = tree.getroot()
    if root.find(".//body[@name='gripper_base']") is not None:
        return

    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ValueError(f"{model_path} has no <worldbody> to attach a gripper to")
    tip = _deepest_body(worldbody)
    if tip is None:
        raise ValueError(f"{model_path} has no body to attach a gripper to")

    base = ET.SubElement(tip, "body", name="gripper_base")
    base.set("pos", "0 0 0.02")
    palm = ET.SubElement(base, "geom")
    palm.set("name", "gripper_palm")
    palm.set("type", "box")
    palm.set("size", "0.035 0.02 0.02")
    palm.set("mass", "0.4")

    for side, sign in (("left", 1.0), ("right", -1.0)):
        finger = ET.SubElement(base, "body", name=f"{side}_finger")
        finger.set("pos", f"{0.03 * sign:.3f} 0 0.04")
        joint = ET.SubElement(finger, "joint", name=f"{side}_finger_joint")
        joint.set("type", "slide")
        joint.set("axis", f"{-sign:.1f} 0 0")
        joint.set("range", "0 0.04")
        joint.set("damping", "5")
        geom = ET.SubElement(finger, "geom")
        geom.set("name", f"{side}_finger_pad")
        geom.set("type", "box")
        geom.set("size", "0.008 0.015 0.03")
        geom.set("mass", "0.05")
        # High tangential friction: a generated gripper with default friction
        # drops everything, which would look like a control bug.
        geom.set("friction", "1.6 0.02 0.001")

    tendon_root = root.find("tendon")
    if tendon_root is None:
        tendon_root = ET.SubElement(root, "tendon")
    fixed = ET.SubElement(tendon_root, "fixed", name="grip_coupling")
    for side in ("left", "right"):
        joint_ref = ET.SubElement(fixed, "joint")
        joint_ref.set("joint", f"{side}_finger_joint")
        joint_ref.set("coef", "1")

    actuator = root.find("actuator")
    if actuator is None:
        actuator = ET.SubElement(root, "actuator")
    grip = ET.SubElement(actuator, "position", name="gripper_act")
    grip.set("tendon", "grip_coupling")
    grip.set("kp", "200")
    grip.set("ctrlrange", "0 0.08")

    _write_xml(root, model_path)


def validate(model_path: Path) -> tuple[bool, str]:
    """Load the model and run a short passive sim. Returns ``(ok, message)``.

    The acceptance gate for anything this module or the Modeler agent produces.
    Checks it compiles, that gravity does not explode it, and that actuators can
    hold a pose against gravity — a model that cannot hold its own arm up will
    fail every scenario for reasons that have nothing to do with the code.
    """
    model_path = Path(model_path)
    try:
        model = mujoco.MjModel.from_xml_path(str(model_path))
    except (ValueError, FileNotFoundError) as exc:
        return False, f"does not compile: {exc}"

    data = mujoco.MjData(model)
    steps = max(1, round(1.0 / model.opt.timestep))
    for _ in range(steps):
        mujoco.mj_step(model, data)
        if not np.all(np.isfinite(data.qpos)) or not np.all(np.isfinite(data.qvel)):
            return False, "passive simulation diverged to non-finite state"
    speed = float(np.max(np.abs(data.qvel))) if model.nv else 0.0
    if speed > 100.0:
        return False, f"passive simulation is unstable (max |qvel| = {speed:.1f})"

    if model.nu == 0:
        return True, "compiles and is passively stable, but has no actuators"

    # Hold-pose test: command the current configuration and see whether the
    # actuators keep it there for a second.
    mujoco.mj_resetData(model, data)
    mujoco.mj_forward(model, data)
    target = np.array(data.qpos[: model.nu], dtype=float)
    start = np.array(data.qpos, dtype=float)
    for _ in range(steps):
        data.ctrl[:] = np.clip(
            target,
            model.actuator_ctrlrange[:, 0],
            model.actuator_ctrlrange[:, 1],
        )
        mujoco.mj_step(model, data)
        if not np.all(np.isfinite(data.qpos)):
            return False, "hold-pose test diverged to non-finite state"
    drift = float(np.max(np.abs(np.asarray(data.qpos) - start))) if model.nq else 0.0
    if drift > 0.5:
        return (
            False,
            f"actuators cannot hold a pose against gravity (drift {drift:.2f})",
        )
    return True, f"compiles, stable, holds pose within {drift:.3f} of the start"


def _meshdir_candidates(urdf_path: Path) -> list[str]:
    """Absolute mesh search roots to try, most likely first."""
    base = urdf_path.parent.resolve()
    candidates = [base]
    for name in ("assets", "meshes", "collision", "visual"):
        for directory in (base / name, base.parent / name):
            if directory.is_dir():
                candidates.append(directory)
    candidates.extend(
        d for d in sorted(base.rglob("*")) if d.is_dir() and d not in candidates
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for directory in candidates[:12]:
        text = str(directory)
        if text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered


def _fill_missing_inertials(urdf_root: ET.Element) -> list[str]:
    """Give inertia-free URDF links a placeholder mass so MuJoCo can compile.

    Returns the names of the links that were patched.
    """
    patched: list[str] = []
    for link in urdf_root.findall("link"):
        if link.find("inertial") is not None:
            continue
        inertial = ET.SubElement(link, "inertial")
        ET.SubElement(inertial, "origin").set("xyz", "0 0 0")
        ET.SubElement(inertial, "mass").set("value", "1.0")
        inertia = ET.SubElement(inertial, "inertia")
        for axis in ("ixx", "iyy", "izz"):
            inertia.set(axis, "0.01")
        for axis in ("ixy", "ixz", "iyz"):
            inertia.set(axis, "0.0")
        patched.append(link.get("name", "?"))
    return patched


def _inject_actuators(xml_path: Path, assumptions: list[str]) -> int:
    """Give every actuated joint a position servo, plus sane joint defaults."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    default = root.find("default")
    if default is None:
        default = ET.Element("default")
        root.insert(0, default)
    joint_default = default.find("joint")
    if joint_default is None:
        joint_default = ET.SubElement(default, "joint")
    joint_default.set("damping", "1.0")
    joint_default.set("armature", "0.1")

    names: list[str] = []
    worldbody = root.find("worldbody")
    joints = list(worldbody.iter("joint")) if worldbody is not None else []
    for i, joint in enumerate(joints):
        if joint.get("type") in {"free", "ball"}:
            continue
        name = joint.get("name")
        if not name:
            name = f"joint{i}"
            joint.set("name", name)
        names.append(name)

    actuator = root.find("actuator")
    if actuator is None:
        actuator = ET.SubElement(root, "actuator")
    existing = {a.get("joint") for a in actuator}
    for name in names:
        if name in existing:
            continue
        position = ET.SubElement(actuator, "position", name=f"{name}_act")
        position.set("joint", name)
        position.set("kp", "300")
        position.set("kv", "30")
    if names:
        assumptions.append(
            f"Added position servos (kp=300, kv=30) for {len(names)} joints; the "
            "URDF described no actuators."
        )
    _write_xml(root, xml_path)
    return len(names)


def _deepest_body(element: ET.Element) -> ET.Element | None:
    """The tip of the longest body chain — the de facto end effector."""
    best: ET.Element | None = None
    best_depth = -1

    def walk(node: ET.Element, depth: int) -> None:
        nonlocal best, best_depth
        children = node.findall("body")
        if not children:
            if depth > best_depth:
                best, best_depth = node, depth
            return
        for child in children:
            walk(child, depth + 1)

    for body in element.findall("body"):
        walk(body, 0)
    return best


def _write_xml(root: ET.Element, path: Path) -> None:
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(path, encoding="unicode", xml_declaration=False)
