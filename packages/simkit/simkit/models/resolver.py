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

from dataclasses import dataclass, field
from pathlib import Path


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
    raise NotImplementedError
    # TODO(build): try config paths, then identify(), then return a miss with
    # a populated report + candidates list.


def identify(repo_dir: Path) -> Resolution:
    """Infer the robot from repo contents without any config help."""
    raise NotImplementedError
    # TODO(build): scan for URDF, then imports, then joint limit tables;
    # score candidates against the Menagerie index.


def parse_urdf(path: Path) -> dict:
    """Extract joint count, names, limits and link lengths from a URDF."""
    raise NotImplementedError
    # TODO(build): ElementTree parse; return a kinematics dict the generator
    # can also consume.


def scan_driver_imports(repo_dir: Path) -> list[str]:
    """Vendor SDK names imported anywhere in the repo."""
    raise NotImplementedError
    # TODO(build): grep import statements against a vendor->robot mapping.
