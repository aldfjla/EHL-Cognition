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

from dataclasses import dataclass, field
from typing import Any


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


def build(spec: SceneSpec) -> Scene:
    """Compile robot + task world into a ready-to-step Scene."""
    raise NotImplementedError
    # TODO(build): generate task MJCF including the robot, mj_compile, resolve
    # handles by name, apply scenario params to the compiled model.


def apply_params(scene: Scene, params: dict[str, Any]) -> None:
    """Apply scenario parameters to an already-compiled model.

    Mutates masses, frictions, initial positions and noise settings in place.
    Anything that cannot be applied post-compile must be reported, not silently
    ignored — a scenario that did not actually randomize is a false pass.
    """
    raise NotImplementedError
    # TODO(build): map param names to model fields; raise on unappliable keys.


def reset(scene: Scene, seed: int) -> None:
    """Reset to the deterministic initial state for ``seed``."""
    raise NotImplementedError
    # TODO(build): mj_resetData, set qpos from the seeded initial pose, forward.


# TODO(build): decide where task worlds live — a `tasks/` directory of MJCF
# fragments keyed by `task.name`, generated once rather than per run.
