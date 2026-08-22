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

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MenagerieModel:
    """One entry in the library."""

    name: str  # directory name, e.g. "franka_emika_panda"
    vendor: str
    model_path: str  # main MJCF, usually <name>.xml or scene.xml
    dof: int | None = None
    kind: str = ""  # arm | quadruped | humanoid | gripper | drone
    description: str = ""


def index(menagerie_dir: Path, refresh: bool = False) -> list[MenagerieModel]:
    """Build (or load a cached) index of every model in the library."""
    raise NotImplementedError
    # TODO(build): walk directories, pick the canonical MJCF per model, read
    # dof by compiling or by counting <joint> elements; cache to index.json.


def get(name: str, menagerie_dir: Path) -> MenagerieModel | None:
    """Exact lookup by directory name."""
    raise NotImplementedError
    # TODO(build): index lookup, return None on miss.


def search(query: str, menagerie_dir: Path, limit: int = 5) -> list[MenagerieModel]:
    """Fuzzy name/vendor search — the "did they mean the Panda?" path."""
    raise NotImplementedError
    # TODO(build): normalised token overlap over name + vendor + description.


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
    raise NotImplementedError
    # TODO(build): score on dof equality plus joint-name token overlap.


def summary_for_prompt(menagerie_dir: Path) -> str:
    """Compact the index into a markdown table for the Modeler's prompt."""
    raise NotImplementedError
    # TODO(build): group by kind, one line per model: name, vendor, dof.
