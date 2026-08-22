"""Decide which physical model represents the robot this code drives.

Responsibility
--------------
Implement the resolution order documented in :mod:`simkit.models`. Return a
model path plus provenance, or an honest miss that tells the Modeler agent what
was already tried.

Inputs:  the customer checkout path, the parsed ``robotci.yaml``.
Outputs: a :class:`Resolution` — source, path, confidence, and a report string.

Identification signals, in order of reliability
-----------------------------------------------
1. An explicit ``robot.menagerie`` or ``robot.model_path`` in the config.
2. A URDF/xacro in the repo — parse it for joint count and link names, which
   usually name the vendor outright.
3. Driver imports and package names (``franka``, ``ur_rtde``, ``pymycobot``).
4. Joint limit tables and DH/calibration constants — a 7-DOF arm with these
   specific limits is identifiable even when nothing is named.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from simkit.models import menagerie

#: Vendor SDK / driver token -> the Menagerie directory that vendor's robot
#: lives in. Signal 3: a repo that imports ``ur_rtde`` drives a UR arm even if
#: nothing else in it says so.
VENDOR_SDKS: dict[str, str] = {
    "franka": "franka_emika_panda",
    "frankx": "franka_emika_panda",
    "panda_py": "franka_emika_panda",
    "libfranka": "franka_emika_panda",
    "ur_rtde": "universal_robots_ur5e",
    "rtde_control": "universal_robots_ur5e",
    "rtde_receive": "universal_robots_ur5e",
    "urx": "universal_robots_ur5e",
    "kortex": "kinova_gen3",
    "kinova": "kinova_gen3",
    "iiwapy": "kuka_iiwa_14",
    "pyfri": "kuka_iiwa_14",
    "xarm": "ufactory_xarm7",
    "pymycobot": "trs_so_arm100",
    "interbotix": "trossen_vx300s",
    "intera": "rethink_robotics_sawyer",
    "bosdyn": "boston_dynamics_spot",
    "unitree_sdk2py": "unitree_go2",
    "unitree_legged_sdk": "unitree_go1",
    "stretch_body": "hello_robot_stretch_3",
    "piper_sdk": "agilex_piper",
    "flexivrdk": "flexiv_rizon4",
    "cflib": "bitcraze_crazyflie_2",
    "allegro": "wonik_allegro",
    "robotiq": "robotiq_2f85",
}

#: Directory names never worth walking in a customer checkout.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "build",
    "dist",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][A-Za-z0-9_.]*)|import\s+([A-Za-z_][A-Za-z0-9_.]*))",
    re.MULTILINE,
)


@dataclass
class Resolution:
    """The outcome of trying to find a model, hit or miss."""

    found: bool
    source: str = ""  # menagerie | repo | generated
    name: str = ""
    model_path: str = ""
    dof: int | None = None
    confidence: float = 0.0
    #: Human-readable account of what was tried. On a miss this is handed to
    #: the Modeler agent verbatim so it does not repeat the same searches.
    report: str = ""
    candidates: list[str] = field(default_factory=list)


def resolve(repo_dir: Path, config: dict) -> Resolution:
    """Run the full resolution order. Never raises — a miss is a valid result."""
    repo_dir = Path(repo_dir)
    robot = (config or {}).get("robot") or {}
    library = menagerie.default_dir()
    tried: list[str] = []

    name = robot.get("menagerie")
    if name:
        model = menagerie.get(str(name), library)
        if model is not None:
            path = menagerie.resolve_model_path(model, library)
            return Resolution(
                found=True,
                source="menagerie",
                name=model.name,
                model_path=str(path),
                dof=model.dof,
                confidence=1.0,
                report=(
                    f"robotci.yaml named Menagerie model {model.name!r} "
                    f"({model.vendor}, {model.dof} dof); used as-is."
                ),
            )
        near = [m.name for m in menagerie.search(str(name), library)]
        tried.append(
            f"robotci.yaml named Menagerie model {name!r}, which is not in the "
            f"local library at {library}"
            + (f" (closest: {', '.join(near)})" if near else "")
        )

    explicit = robot.get("model_path")
    if explicit:
        candidate = (repo_dir / str(explicit)).resolve()
        if candidate.is_file():
            return Resolution(
                found=True,
                source="repo",
                name=candidate.stem,
                model_path=str(candidate),
                dof=menagerie.count_joints(candidate),
                confidence=1.0,
                report=f"robotci.yaml named in-repo model {explicit!r}; used as-is.",
            )
        tried.append(f"robotci.yaml named robot.model_path {explicit!r}, absent")

    identified = identify(repo_dir)
    if identified.found:
        identified.report = "\n".join([*tried, identified.report]).strip()
        return identified

    tried.append(identified.report)
    fallback = robot.get("fallback") or {}
    if fallback:
        tried.append(
            "robotci.yaml fallback hints available for generation: "
            + ", ".join(f"{k}={v}" for k, v in sorted(fallback.items()))
        )
    return Resolution(
        found=False,
        source="",
        confidence=0.0,
        report="\n".join(t for t in tried if t),
        candidates=identified.candidates,
    )


def identify(repo_dir: Path) -> Resolution:
    """Infer the robot from repo contents without any config help."""
    repo_dir = Path(repo_dir)
    library = menagerie.default_dir()
    notes: list[str] = []
    scored: dict[str, float] = {}
    kinematics: dict = {}

    urdf = find_urdf(repo_dir)
    if urdf is not None:
        kinematics = parse_urdf(urdf)
        notes.append(
            f"found URDF {urdf.relative_to(repo_dir)}: {kinematics.get('dof', 0)} "
            f"joints, links {', '.join(kinematics.get('link_names', [])[:4])}"
        )
        for model, score in menagerie.match_kinematics(
            int(kinematics.get("dof") or 0),
            list(kinematics.get("joint_names") or []),
            library,
            limit=5,
        ):
            scored[model.name] = max(scored.get(model.name, 0.0), score)
    else:
        notes.append("no URDF or xacro in the repo")

    sdks = scan_driver_imports(repo_dir)
    if sdks:
        notes.append(f"driver imports: {', '.join(sdks)}")
        for sdk in sdks:
            target = VENDOR_SDKS[sdk]
            if menagerie.get(target, library) is not None:
                # An imported vendor SDK is a stronger signal than joint counts:
                # nobody imports ur_rtde to drive a Panda.
                scored[target] = max(scored.get(target, 0.0), 0.85)
    else:
        notes.append("no known vendor SDK imported")

    limits = scan_joint_limits(repo_dir)
    if limits:
        notes.append(f"joint limit table for {len(limits)} joints in the control code")
        for model, score in menagerie.match_kinematics(len(limits), [], library):
            scored[model.name] = max(scored.get(model.name, 0.0), score * 0.7)

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    candidates = [name for name, _ in ranked[:5]]
    if ranked and ranked[0][1] >= 0.6:
        best = menagerie.get(ranked[0][0], library)
        if best is not None:
            return Resolution(
                found=True,
                source="menagerie",
                name=best.name,
                model_path=str(menagerie.resolve_model_path(best, library)),
                dof=best.dof,
                confidence=round(min(ranked[0][1], 0.95), 3),
                report="identified from repo contents: "
                + "; ".join(notes)
                + f" -> {best.name}",
                candidates=candidates,
            )
    return Resolution(
        found=False,
        report="automatic identification found nothing conclusive: "
        + "; ".join(notes)
        + (
            f"; best library candidates were {', '.join(candidates)}"
            if candidates
            else "; no library candidate scored at all"
        ),
        candidates=candidates,
    )


def find_urdf(repo_dir: Path) -> Path | None:
    """The most likely robot description file in a checkout, if any."""
    found: list[Path] = []
    for path in _walk(repo_dir):
        if path.suffix in {".urdf", ".xacro"}:
            found.append(path)
    if not found:
        return None
    # Prefer a plain URDF over a xacro (no macro expansion needed) and a
    # shallower path over a deeper one.
    return min(found, key=lambda p: (p.suffix != ".urdf", len(p.parts), p.name))


def parse_urdf(path: Path) -> dict:
    """Extract joint count, names, limits and link lengths from a URDF.

    The returned dict is exactly what :func:`simkit.models.generator.from_kinematics`
    consumes, so an unmatched URDF flows straight into generation.
    """
    root = ET.parse(path).getroot()
    joints: list[dict] = []
    for joint in root.findall("joint"):
        joint_type = joint.get("type", "revolute")
        if joint_type == "fixed":
            continue
        limit = joint.find("limit")
        axis = joint.find("axis")
        origin = joint.find("origin")
        entry: dict = {
            "name": joint.get("name", f"joint{len(joints)}"),
            "type": joint_type,
            "axis": _floats(axis.get("xyz") if axis is not None else "0 0 1", 3),
            "origin": _floats(origin.get("xyz") if origin is not None else "0 0 0", 3),
        }
        if limit is not None:
            entry["lower"] = float(limit.get("lower", "-3.14159"))
            entry["upper"] = float(limit.get("upper", "3.14159"))
            if limit.get("effort") is not None:
                entry["effort"] = float(limit.get("effort"))
            if limit.get("velocity") is not None:
                entry["velocity"] = float(limit.get("velocity"))
        joints.append(entry)

    links = [link.get("name", "") for link in root.findall("link")]
    link_lengths: list[float] = []
    for joint in joints:
        offset = joint["origin"]
        link_lengths.append(sum(v * v for v in offset) ** 0.5)

    return {
        "robot_name": root.get("name", path.stem),
        "source": str(path),
        "dof": len(joints),
        "joint_names": [j["name"] for j in joints],
        "joints": joints,
        "link_names": links,
        "link_lengths": link_lengths,
    }


def scan_driver_imports(repo_dir: Path) -> list[str]:
    """Vendor SDK names imported anywhere in the repo."""
    hits: set[str] = set()
    for path in _walk(repo_dir):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for match in _IMPORT_RE.finditer(text):
            module = (match.group(1) or match.group(2) or "").split(".")[0]
            if module in VENDOR_SDKS:
                hits.add(module)
    return sorted(hits)


def scan_joint_limits(repo_dir: Path) -> dict[str, tuple[float, float]]:
    """Signal 4: a joint limit table written straight into the control code.

    Matches the shapes teams actually write — ``JOINT_LIMITS = [(-2.9, 2.9), ...]``
    or ``Q_MIN = [...]`` / ``Q_MAX = [...]`` — and returns one entry per joint.
    """
    number = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
    pair_re = re.compile(rf"[(\[]\s*({number})\s*,\s*({number})\s*[)\]]")
    # Non-greedy to the first closing bracket: the elements are tuples, so the
    # first ``]`` or ``}`` really is the end of the table.
    table_re = re.compile(
        r"(?:JOINT_LIMITS|joint_limits|Q_LIMITS|LIMITS)\s*=\s*[\[{](.*?)[\]}]",
        re.DOTALL,
    )
    vector_re = re.compile(
        rf"\b(Q_MIN|Q_MAX|q_min|q_max|LOWER_LIMITS|UPPER_LIMITS)\s*=\s*[\[(]"
        rf"((?:\s*{number}\s*,?)+)[\])]"
    )
    for path in _walk(repo_dir):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for table in table_re.finditer(text):
            pairs = pair_re.findall(table.group(1))
            if len(pairs) >= 4:
                return {
                    f"joint{i}": (float(low), float(high))
                    for i, (low, high) in enumerate(pairs)
                }
        vectors = {
            match.group(1).lower(): [
                float(v) for v in re.findall(number, match.group(2))
            ]
            for match in vector_re.finditer(text)
        }
        lows = vectors.get("q_min") or vectors.get("lower_limits") or []
        highs = vectors.get("q_max") or vectors.get("upper_limits") or []
        if len(lows) >= 4 and len(lows) == len(highs):
            return {
                f"joint{i}": (low, high)
                for i, (low, high) in enumerate(zip(lows, highs, strict=True))
            }
    return {}


def _walk(root: Path, limit: int = 5000):
    """Yield files under ``root``, skipping vendored and generated trees."""
    seen = 0
    stack = [Path(root)]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (OSError, NotADirectoryError):
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
                continue
            seen += 1
            if seen > limit:
                return
            yield entry


def _floats(text: str, count: int) -> list[float]:
    parts = [p for p in re.split(r"[\s,]+", (text or "").strip()) if p]
    values = [float(p) for p in parts[:count]]
    while len(values) < count:
        values.append(0.0)
    return values
