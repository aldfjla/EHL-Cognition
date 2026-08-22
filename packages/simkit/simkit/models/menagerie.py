"""Local index and lookup over the MuJoCo Menagerie model library.

Responsibility
--------------
Make ``vendor/menagerie`` queryable: what models exist, how many DOF each has,
which vendor made it, and where its main MJCF file is.

Inputs:  ``MENAGERIE_DIR`` (populated by ``scripts/fetch_menagerie.sh``).
Outputs: :class:`MenagerieModel` records and fuzzy lookup by name or by
         kinematic signature.

Why an index
------------
Menagerie is a few hundred MB of XML across dozens of directories. Scanning it
per lookup is slow, and — more importantly — the index is small enough to paste
into the Modeler agent's prompt, which is what lets the agent name a model
instead of cloning the library to go looking.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

#: Filename of the cached index inside ``MENAGERIE_DIR``.
INDEX_FILENAME = "index.json"

#: Menagerie's README groups models under these headings; the heading is the
#: most reliable ``kind`` signal available, being maintained by the library.
_README_KINDS = {
    "humanoids": "humanoid",
    "quadrupeds": "quadruped",
    "bipeds": "biped",
    "biomechanical": "biomechanical",
    "dual arms": "dual_arm",
    "arms": "arm",
    "end-effectors": "gripper",
    "end effectors": "gripper",
    "grippers": "gripper",
    "hands": "gripper",
    "mobile manipulators": "mobile_manipulator",
    "drones": "drone",
    "mobile bases": "mobile_base",
    "misc": "misc",
    "miscellaneous": "misc",
}

#: Fallback ``kind`` classification when the README says nothing about a model.
_KIND_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("hand", "gripper"),
    ("gripper", "gripper"),
    ("allegro", "gripper"),
    ("robotiq", "gripper"),
    ("softfoot", "biomechanical"),
    ("humanoid", "humanoid"),
    ("anymal", "quadruped"),
    ("barkour", "quadruped"),
    ("spot", "quadruped"),
    ("go1", "quadruped"),
    ("go2", "quadruped"),
    ("a1", "quadruped"),
    ("cassie", "biped"),
    ("crazyflie", "drone"),
    ("x2", "drone"),
    ("stretch", "mobile_manipulator"),
    ("tidybot", "mobile_manipulator"),
    ("tiago", "mobile_manipulator"),
    ("arm", "arm"),
    ("panda", "arm"),
    ("ur5e", "arm"),
    ("ur10e", "arm"),
    ("iiwa", "arm"),
    ("gen3", "arm"),
    ("piper", "arm"),
    ("rizon", "arm"),
    ("sawyer", "arm"),
    ("lite6", "arm"),
    ("z1", "arm"),
)

#: Vendor names that are more than the first token of the directory name.
_VENDOR_PREFIXES = (
    "franka_emika",
    "universal_robots",
    "boston_dynamics",
    "rethink_robotics",
    "rainbow_robotics",
    "hello_robot",
    "anybotics",
    "bitcraze",
    "pndbotics",
    "apptronik",
    "robotstudio",
    "toddlerbot",
    "stanford",
    "berkeley",
    "ufactory",
    "trossen",
    "agility",
    "agilex",
    "tetheria",
    "unitree",
    "robotis",
    "flexiv",
    "kinova",
    "sharpa",
    "shadow",
    "fourier",
    "booster",
    "seeed",
    "google",
    "skydio",
    "flybody",
    "kuka",
    "wonik",
    "franka",
    "i2rt",
    "arx",
    "pal",
    "iit",
    "trs",
    "umi",
    "ms",
)

#: Directories in the checkout that are not robot models.
_SKIP_DIRS = {"assets", "test", "doc", "docs", ".github"}


@dataclass
class MenagerieModel:
    """One entry in the library."""

    name: str  # directory name, e.g. "franka_emika_panda"
    vendor: str
    model_path: str  # main MJCF, usually <name>.xml or scene.xml
    dof: int | None = None
    kind: str = ""  # arm | quadruped | humanoid | gripper | drone
    description: str = ""


def default_dir() -> Path:
    """Where the library lives, honouring ``MENAGERIE_DIR``."""
    env = os.environ.get("MENAGERIE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / "vendor" / "menagerie"


def index(menagerie_dir: Path, refresh: bool = False) -> list[MenagerieModel]:
    """Build (or load a cached) index of every model in the library.

    The cache is ``<menagerie_dir>/index.json``. ``scripts/fetch_menagerie.sh``
    writes it after cloning so the first lookup of a fresh checkout is already
    cheap; ``refresh=True`` rebuilds it in place.
    """
    menagerie_dir = Path(menagerie_dir)
    cache = menagerie_dir / INDEX_FILENAME
    if not refresh and cache.is_file():
        try:
            payload = json.loads(cache.read_text())
            return [MenagerieModel(**entry) for entry in payload["models"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass  # corrupt cache is a rebuild, not an error

    models = build_index(menagerie_dir)
    try:
        cache.write_text(
            json.dumps(
                {"version": 1, "models": [asdict(m) for m in models]},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    except OSError:
        pass  # a read-only library is still usable, just not cacheable
    return models


def build_index(menagerie_dir: Path) -> list[MenagerieModel]:
    """Walk the checkout and describe every model. Ignores any cache."""
    menagerie_dir = Path(menagerie_dir)
    if not menagerie_dir.is_dir():
        return []

    readme = _parse_readme(menagerie_dir / "README.md")
    models: list[MenagerieModel] = []
    for directory in sorted(p for p in menagerie_dir.iterdir() if p.is_dir()):
        if directory.name.startswith(".") or directory.name in _SKIP_DIRS:
            continue
        meta = readme.get(directory.name, {})
        mjcf = _canonical_mjcf(directory, meta.get("file", ""))
        if mjcf is None:
            continue
        dof = meta.get("dof")
        if dof is None:
            dof = count_joints(mjcf)
        models.append(
            MenagerieModel(
                name=directory.name,
                vendor=_vendor_for(directory.name, meta.get("title", "")),
                model_path=str(mjcf.relative_to(menagerie_dir)),
                dof=dof,
                kind=meta.get("kind") or _kind_for(directory.name),
                description=meta.get("title", "") or _titleize(directory.name),
            )
        )
    return models


def get(name: str, menagerie_dir: Path) -> MenagerieModel | None:
    """Exact lookup by directory name."""
    wanted = _normalise(name)
    for model in index(menagerie_dir):
        if _normalise(model.name) == wanted:
            return model
    return None


def search(query: str, menagerie_dir: Path, limit: int = 5) -> list[MenagerieModel]:
    """Fuzzy name/vendor search — the "did they mean the Panda?" path."""
    tokens = _tokens(query)
    if not tokens:
        return []
    scored: list[tuple[float, str, MenagerieModel]] = []
    for model in index(menagerie_dir):
        haystack = _tokens(f"{model.name} {model.vendor} {model.description}")
        if not haystack:
            continue
        overlap = len(tokens & haystack) / len(tokens)
        # A substring hit catches "ur5e" inside "universal_robots_ur5e".
        substring = 0.5 if any(t in model.name.lower() for t in tokens) else 0.0
        score = overlap + substring
        if score > 0:
            scored.append((score, model.name, model))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [model for _, _, model in scored[:limit]]


def match_kinematics(
    dof: int,
    joint_names: list[str],
    menagerie_dir: Path,
    limit: int = 3,
) -> list[tuple[MenagerieModel, float]]:
    """Rank models by similarity to an observed kinematic signature.

    Returns ``(model, score)`` pairs. Used when the repo names no vendor but
    does expose joint counts and names.
    """
    observed = _tokens(" ".join(joint_names))
    scored: list[tuple[float, str, MenagerieModel]] = []
    for model in index(menagerie_dir):
        score = 0.0
        if model.dof is not None and dof > 0:
            if model.dof == dof:
                score += 0.6
            elif abs(model.dof - dof) <= 2:
                # Menagerie counts gripper DOF in the total, so a 7-DOF arm
                # often indexes as 8 or 9. Near misses are real candidates.
                score += 0.35
        if observed:
            haystack = _tokens(f"{model.name} {model.description}")
            score += 0.4 * (len(observed & haystack) / len(observed))
        if score > 0:
            scored.append((score, model.name, model))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(model, round(score, 3)) for score, _, model in scored[:limit]]


def summary_for_prompt(menagerie_dir: Path) -> str:
    """Compact the index into a markdown table for the Modeler's prompt."""
    models = index(menagerie_dir)
    if not models:
        return "(no local Menagerie checkout: run `make menagerie`)"
    by_kind: dict[str, list[MenagerieModel]] = {}
    for model in models:
        by_kind.setdefault(model.kind or "other", []).append(model)

    lines: list[str] = []
    for kind in sorted(by_kind):
        lines.append(f"### {kind}")
        lines.append("| name | vendor | dof |")
        lines.append("|---|---|---|")
        for model in sorted(by_kind[kind], key=lambda m: m.name):
            dof = "?" if model.dof is None else str(model.dof)
            lines.append(f"| {model.name} | {model.vendor} | {dof} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def resolve_model_path(model: MenagerieModel, menagerie_dir: Path) -> Path:
    """Absolute path to a model's MJCF."""
    return (Path(menagerie_dir) / model.model_path).resolve()


# -- index construction internals ------------------------------------------- #


def _canonical_mjcf(directory: Path, readme_file: str = "") -> Path | None:
    """The MJCF that describes the robot itself, not a scene wrapping it.

    Menagerie's convention is ``<model>.xml`` for the robot and ``scene.xml``
    for a world that includes it. The robot file is what we want: our task world
    plays the role of ``scene.xml``.

    ``readme_file`` is the XML the library's own gallery links to, which is the
    only signal that distinguishes ``panda.xml`` from ``panda_nohand.xml`` or
    ``hand.xml``. When it points at a scene, follow the scene's ``<include>``
    down to the robot.
    """
    if readme_file:
        linked = directory / readme_file
        if linked.is_file():
            unwrapped = _unwrap_scene(linked)
            if unwrapped is not None:
                return unwrapped
    candidates = [
        p
        for p in sorted(directory.glob("*.xml"))
        if not p.name.startswith("mjx_") and not p.stem.endswith("_mjx")
    ]
    if not candidates:
        return None
    named = [p for p in candidates if p.stem == directory.name]
    if named:
        return named[0]
    robots = [p for p in candidates if not p.stem.startswith("scene")]
    if robots:
        # Shortest stem: "panda.xml" over "panda_nohand.xml".
        return min(robots, key=lambda p: (len(p.stem), p.stem))
    return _unwrap_scene(candidates[0]) or candidates[0]


def _unwrap_scene(mjcf: Path, depth: int = 0) -> Path | None:
    """Follow a ``scene*.xml`` down to the robot MJCF it includes."""
    if not mjcf.stem.startswith("scene"):
        return mjcf
    if depth > 3:
        return None
    try:
        root = ET.parse(mjcf).getroot()
    except (ET.ParseError, OSError):
        return None
    for element in root.iter("include"):
        included = mjcf.parent / element.get("file", "")
        if included.is_file() and included.suffix == ".xml":
            return _unwrap_scene(included, depth + 1)
    return None


def count_joints(mjcf: Path) -> int | None:
    """Count actuated joints in an MJCF without compiling it.

    Follows ``<include>`` directives, skips ``<default>`` blocks (those declare
    classes, not joints) and ignores free joints, which are not DOF a controller
    commands.
    """
    try:
        root = ET.parse(mjcf).getroot()
    except (ET.ParseError, OSError):
        return None

    total = 0
    for element in root.iter():
        tag = element.tag
        if tag == "default":
            continue
        if tag == "include":
            child = mjcf.parent / element.get("file", "")
            if child.is_file():
                nested = count_joints(child)
                total += nested or 0
    total += _count_joints_outside_defaults(root)
    return total


def _count_joints_outside_defaults(element: ET.Element) -> int:
    total = 0
    for child in element:
        if child.tag == "default":
            continue
        if child.tag == "joint" and child.get("type") != "free":
            total += 1
        total += _count_joints_outside_defaults(child)
    return total


def _parse_readme(readme: Path) -> dict[str, dict]:
    """Harvest kind/DOF/title per model from Menagerie's own gallery tables.

    The library maintains these; inferring the same facts from XML would be both
    more code and less accurate.
    """
    if not readme.is_file():
        return {}
    try:
        text = readme.read_text(errors="replace")
    except OSError:
        return {}

    meta: dict[str, dict] = {}
    kind = ""
    for line in text.splitlines():
        heading = re.match(r"^\*\*([A-Za-z ,\-]+)\.\*\*\s*$", line.strip())
        if heading:
            kind = _README_KINDS.get(heading.group(1).strip().lower(), "")
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        directories = re.findall(r"mujoco_menagerie/main/([A-Za-z0-9_]+)/", cells[0])
        if not directories:
            directories = re.findall(r"\(([A-Za-z0-9_]+)/LICENSE\)", line)
        if not directories:
            continue
        directory_name = directories[0]
        if directory_name in meta:
            # A model can appear twice — the Panda is both an arm and, via
            # hand.xml, a gripper. The first row is the whole robot.
            continue
        linked = re.search(
            r"mujoco_menagerie/main/[A-Za-z0-9_]+/([A-Za-z0-9_.]+\.xml)", cells[0]
        )
        entry: dict = {
            "kind": kind,
            "title": cells[1],
            "file": linked.group(1) if linked else "",
        }
        dof = re.fullmatch(r"\d+", cells[2]) if len(cells) > 2 else None
        if dof:
            value = int(dof.group(0))
            # Menagerie lists 0 DOF for free-flying drones; that is a real
            # answer about actuated joints, so keep it.
            entry["dof"] = value
        meta[directory_name] = entry
    return meta


def _vendor_for(name: str, title: str) -> str:
    for prefix in _VENDOR_PREFIXES:
        if name.startswith(prefix + "_") or name == prefix:
            return _titleize(prefix)
    if title:
        return title.split()[0]
    return _titleize(name.split("_")[0])


def _kind_for(name: str) -> str:
    lowered = name.lower()
    for token, kind in _KIND_KEYWORDS:
        if token in lowered:
            return kind
    return ""


def _titleize(text: str) -> str:
    return " ".join(part.capitalize() for part in text.split("_"))


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}
