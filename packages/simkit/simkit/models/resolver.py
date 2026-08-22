"""Decide which physical model represents the robot this code drives.

Responsibility
--------------
Implement the resolution order documented in :mod:`simkit.models`. Return a
model path plus provenance, or an honest miss that tells the Modeler agent what
was already tried.

Inputs:  the customer checkout path, the parsed ``robotci.yaml``.
Outputs: a :class:`Resolution` — source, path, provenance, and a report string.

Identification signals, in order of reliability
-----------------------------------------------
1. An explicit ``robot.menagerie`` or ``robot.model_path`` in the config.
2. A compiling, actuated MJCF shipped by the repo.
3. A repo URDF/xacro converted to MJCF and validated by MuJoCo.
4. Vendor imports, dependency manifests and documentation matched to Menagerie.
5. Kinematic similarity against Menagerie.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
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
    ".robotci",
    "artifacts",
    "fixtures",
    "test",
    "tests",
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
    provenance: str | None = None
    license: str | None = None
    processing_steps: list[str] = field(default_factory=list)
    approximate: bool = False
    cache_hit: bool = False


def resolve(
    repo_dir: Path,
    config: dict,
    output_dir: Path | None = None,
    cache_dir: Path | None = None,
    repo_identity: str | None = None,
) -> Resolution:
    """Run the full resolution order. Never raises — a miss is a valid result."""
    repo_dir = Path(repo_dir).resolve()
    robot = (config or {}).get("robot") or {}
    library = menagerie.default_dir()
    tried: list[str] = []
    identity = repo_identity or str(repo_dir.resolve())
    fingerprint = fingerprint_inputs(repo_dir, robot)

    if cache_dir is not None:
        cached = _load_cache(Path(cache_dir), identity, fingerprint)
        if cached is not None:
            return cached

    name = robot.get("menagerie")
    if name:
        model = menagerie.get(str(name), library)
        if model is not None:
            path = menagerie.resolve_model_path(model, library)
            result = Resolution(
                found=True,
                source="menagerie",
                name=model.name,
                model_path=str(path),
                dof=model.dof,
                confidence=1.0,
                provenance=f"robotci.yaml robot.menagerie={model.name}",
                license=menagerie.read_license(model, library),
                processing_steps=["Menagerie lookup"],
                report=(
                    f"robotci.yaml named Menagerie model {model.name!r} "
                    f"({model.vendor}, {model.dof} dof); used as-is."
                ),
            )
            return _store_cache(result, cache_dir, identity, fingerprint)
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
            valid, detail, dof = _validate_mjcf(candidate, require_actuators=False)
            if valid:
                result = Resolution(
                    found=True,
                    source="repo",
                    name=candidate.stem,
                    model_path=str(candidate),
                    dof=dof,
                    confidence=1.0,
                    provenance=f"robotci.yaml robot.model_path={explicit}",
                    processing_steps=["MJCF compile", "MJCF validation"],
                    report=f"robotci.yaml named in-repo model {explicit!r}; used as-is.",
                )
                return _store_cache(result, cache_dir, identity, fingerprint)
            tried.append(
                f"robotci.yaml model_path {explicit!r} is unavailable: {detail}"
            )
        else:
            tried.append(f"robotci.yaml named robot.model_path {explicit!r}, absent")

    for candidate in find_mjcf(repo_dir):
        valid, detail, dof = _validate_mjcf(candidate, require_actuators=True)
        if not valid:
            tried.append(
                f"rejected repo MJCF {candidate.relative_to(repo_dir)}: {detail}"
            )
            continue
        result = Resolution(
            found=True,
            source="repo",
            name=candidate.stem,
            model_path=str(candidate),
            dof=dof,
            confidence=0.98,
            provenance=f"repo MJCF {candidate.relative_to(repo_dir)}",
            processing_steps=["MJCF compile", "MJCF validation"],
            report=(
                f"selected repo MJCF {candidate.relative_to(repo_dir)}; "
                "it compiled, contains bodies and actuators, and passed validation."
            ),
        )
        return _store_cache(result, cache_dir, identity, fingerprint)

    if output_dir is not None:
        converted, conversion_notes = _convert_urdfs(
            repo_dir, find_urdfs(repo_dir), Path(output_dir)
        )
        tried.extend(conversion_notes)
        if converted is not None:
            return _store_cache(converted, cache_dir, identity, fingerprint)
    elif find_urdfs(repo_dir):
        tried.append("URDF/xacro found but no output_dir was supplied for conversion")

    identified = identify(repo_dir)
    if identified.found:
        identified.report = "\n".join([*tried, identified.report]).strip()
        return _store_cache(identified, cache_dir, identity, fingerprint)

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
    repo_dir = Path(repo_dir).resolve()
    library = menagerie.default_dir()
    notes: list[str] = []
    scored: dict[str, float] = {}
    kinematics: dict = {}

    urdf = find_urdf(repo_dir)
    if urdf is not None:
        try:
            kinematics = parse_urdf(urdf)
        except (OSError, ET.ParseError, ValueError) as exc:
            notes.append(
                f"URDF {urdf.relative_to(repo_dir)} could not be parsed: {exc}"
            )
            kinematics = {}
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
                # Imports name a vendor, but do not measure the hardware.
                scored[target] = max(scored.get(target, 0.0), 0.85)
    else:
        notes.append("no known vendor SDK imported")

    limits = scan_joint_limits(repo_dir)
    if limits:
        notes.append(f"joint limit table for {len(limits)} joints in the control code")
        for model, score in menagerie.match_kinematics(len(limits), [], library):
            scored[model.name] = max(scored.get(model.name, 0.0), score * 0.7)

    for model, source, matched in scan_name_matches(repo_dir, library):
        notes.append(f"{source} mentions {matched!r}")
        scored[model.name] = max(scored.get(model.name, 0.0), 0.35)

    ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    candidates = [name for name, _ in ranked[:5]]
    if ranked and ranked[0][1] >= 0.3:
        best = menagerie.get(ranked[0][0], library)
        if best is not None:
            return Resolution(
                found=True,
                source="menagerie",
                name=best.name,
                model_path=str(menagerie.resolve_model_path(best, library)),
                dof=best.dof,
                confidence=round(min(ranked[0][1], 0.95), 3),
                provenance=f"Menagerie entry {best.name}; inferred from repo signals",
                license=menagerie.read_license(best, library),
                processing_steps=["Menagerie lookup"],
                approximate=True,
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
    found = find_urdfs(repo_dir)
    if not found:
        return None
    # Prefer a plain URDF over a xacro (no macro expansion needed) and a
    # shallower path over a deeper one.
    return min(found, key=lambda p: (p.suffix != ".urdf", len(p.parts), p.name))


def find_urdfs(repo_dir: Path) -> list[Path]:
    """Return all candidate URDF/xacro files in deterministic order."""
    found = [path for path in _walk(repo_dir) if path.suffix in {".urdf", ".xacro"}]
    return sorted(found, key=lambda p: (p.suffix != ".urdf", len(p.parts), p.name))


def find_mjcf(repo_dir: Path) -> list[Path]:
    """Find repo MJCF roots, excluding tests, fixtures and vendored trees."""
    candidates: list[Path] = []
    for path in _walk(repo_dir):
        if path.suffix.lower() != ".xml":
            continue
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        if root.tag.rsplit("}", 1)[-1] == "mujoco":
            candidates.append(path)
    return sorted(candidates, key=lambda p: (len(p.parts), p.name))


def fingerprint_inputs(repo_dir: Path, robot_config: dict) -> str:
    """Hash model-relevant checkout inputs and the robot config block."""
    digest = hashlib.sha256()
    digest.update(
        json.dumps(robot_config or {}, sort_keys=True, separators=(",", ":")).encode()
    )
    for path in _walk(repo_dir):
        try:
            relative = path.relative_to(repo_dir).as_posix()
            payload = path.read_bytes()
        except (OSError, ValueError):
            continue
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def default_cache_dir() -> Path:
    """Durable cache location, outside per-run artifact directories."""
    configured = os.environ.get("MODEL_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return (
        Path(os.environ.get("ARTIFACTS_DIR", "artifacts")).expanduser() / "model-cache"
    )


def scan_name_matches(
    repo_dir: Path, menagerie_dir: Path
) -> list[tuple[menagerie.MenagerieModel, str, str]]:
    """Match distinctive model/vendor names in manifests and prose."""
    files = [
        path
        for path in _walk(repo_dir)
        if path.name.lower()
        in {
            "readme",
            "readme.md",
            "readme.rst",
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "package.xml",
        }
        or path.name.lower().startswith("readme.")
        or (
            path.suffix.lower() in {".md", ".rst"}
            and "docs" in {part.lower() for part in path.relative_to(repo_dir).parts}
        )
    ]
    matches: list[tuple[menagerie.MenagerieModel, str, str]] = []
    for path in files:
        try:
            text = path.read_text(errors="replace").lower()
        except OSError:
            continue
        is_prose = path.name.lower().startswith("readme") or path.suffix.lower() in {
            ".md",
            ".rst",
        }
        source = "README/docs" if is_prose else "dependency manifest"
        for model in menagerie.index(menagerie_dir):
            tokens = [
                token
                for token in re.split(r"[^a-z0-9]+", model.name.lower())
                if len(token) >= 4
            ]
            matched = next((token for token in tokens if token in text), None)
            if matched is not None:
                matches.append((model, source, matched))
    return matches


def _convert_urdfs(
    repo_dir: Path, candidates: list[Path], output_dir: Path
) -> tuple[Resolution | None, list[str]]:
    """Convert the first valid URDF/xacro, retaining honest failure notes."""
    from simkit.models import generator

    output_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    for source in candidates:
        urdf = source
        steps: list[str] = []
        if source.suffix == ".xacro":
            executable = shutil.which("xacro")
            if executable is None:
                notes.append(
                    f"xacro {source.relative_to(repo_dir)} skipped: xacro is unavailable"
                )
                continue
            expanded = output_dir / f"{source.stem}_expanded.urdf"
            try:
                completed = subprocess.run(
                    [executable, str(source)],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as exc:
                notes.append(f"xacro {source.relative_to(repo_dir)} failed: {exc}")
                continue
            if completed.returncode != 0 or not completed.stdout.strip():
                detail = completed.stderr.strip() or "xacro produced no XML"
                notes.append(f"xacro {source.relative_to(repo_dir)} failed: {detail}")
                continue
            try:
                expanded.write_text(completed.stdout)
            except OSError as exc:
                notes.append(f"xacro {source.relative_to(repo_dir)} failed: {exc}")
                continue
            urdf = expanded
            steps.append("xacro expansion")
        try:
            generated = generator.from_urdf(urdf, output_dir)
            valid, detail, dof = _validate_mjcf(
                Path(generated.model_path), require_actuators=True
            )
        except (OSError, ET.ParseError, ValueError) as exc:
            notes.append(
                f"URDF conversion {source.relative_to(repo_dir)} failed: {exc}"
            )
            continue
        if not valid:
            notes.append(
                f"URDF conversion {source.relative_to(repo_dir)} failed validation: {detail}"
            )
            continue
        steps.extend(["URDF compile", "MJCF output validation"])
        return (
            Resolution(
                found=True,
                source="repo",
                name=Path(generated.model_path).stem,
                model_path=generated.model_path,
                dof=dof,
                confidence=0.92,
                provenance=f"converted repo {source.relative_to(repo_dir)}",
                processing_steps=steps,
                report=(
                    f"converted {source.relative_to(repo_dir)} to MJCF and validated "
                    "the compiled model with actuators."
                ),
            ),
            notes,
        )
    return None, notes


def _validate_mjcf(path: Path, require_actuators: bool) -> tuple[bool, str, int | None]:
    """Validate an MJCF through the shared generator gate."""
    import mujoco

    from simkit.models import generator

    try:
        model = mujoco.MjModel.from_xml_path(str(path))
    except (ValueError, FileNotFoundError) as exc:
        return False, f"does not compile: {exc}", None
    if model.nbody <= 1:
        return False, "contains no robot bodies", None
    if require_actuators and model.nu == 0:
        return False, "contains no actuators", None
    ok, detail = generator.validate(path)
    if not ok:
        return False, detail, None
    return True, detail, int(model.nv)


def _cache_path(cache_dir: Path, identity: str, fingerprint: str) -> Path:
    key = hashlib.sha256(f"{identity}\0{fingerprint}".encode()).hexdigest()
    return cache_dir / f"{key}.json"


def _load_cache(cache_dir: Path, identity: str, fingerprint: str) -> Resolution | None:
    path = _cache_path(cache_dir, identity, fingerprint)
    try:
        payload = json.loads(path.read_text())
        model_path = Path(payload["model_path"])
        if not model_path.is_file():
            return None
        import mujoco

        mujoco.MjModel.from_xml_path(str(model_path))
        payload["cache_hit"] = True
        payload["report"] = f"cache hit; {payload.get('report', '')}".strip()
        payload["provenance"] = (
            f"{payload.get('provenance')}; cache hit"
            if payload.get("provenance")
            else "cache hit"
        )
        return Resolution(**payload)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _store_cache(
    result: Resolution,
    cache_dir: Path | None,
    identity: str,
    fingerprint: str,
) -> Resolution:
    if cache_dir is None or not result.found:
        return result
    path = _cache_path(Path(cache_dir), identity, fingerprint)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(result.__dict__)
        source_path = Path(result.model_path)
        if source_path.is_file():
            durable_path = path.parent / "models" / f"{path.stem}{source_path.suffix}"
            durable_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, durable_path)
            payload["model_path"] = str(durable_path)
        path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass
    return result


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
