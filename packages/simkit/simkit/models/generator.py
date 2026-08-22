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

from dataclasses import dataclass, field
from pathlib import Path


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
    raise NotImplementedError
    # TODO(build): mujoco URDF compile, then inject actuators per joint,
    # armature/damping defaults, and a contact exclusion set.


def from_kinematics(spec: dict, out_dir: Path) -> GeneratedModel:
    """Build a serial-chain MJCF from link lengths and joint axes/limits."""
    raise NotImplementedError
    # TODO(build): emit nested <body>/<joint>/<geom> chain, capsule links,
    # mass from length*density, actuators sized to hold the payload.


def add_gripper(model_path: Path, kind: str = "parallel_jaw") -> None:
    """Attach a generic gripper to the end effector."""
    raise NotImplementedError
    # TODO(build): append a parallel-jaw body pair with a tendon-coupled
    # actuator and friction geoms.


def validate(model_path: Path) -> tuple[bool, str]:
    """Load the model and run a short passive sim. Returns ``(ok, message)``.

    The acceptance gate for anything this module or the Modeler agent produces.
    Checks it compiles, that gravity does not explode it, and that actuators can
    hold a pose against gravity — a model that cannot hold its own arm up will
    fail every scenario for reasons that have nothing to do with the code.
    """
    raise NotImplementedError
    # TODO(build): MjModel.from_xml_path, 1s passive step, assert finite qpos
    # and bounded velocities; then a hold-pose test under PD control.
