"""Evaluate success criteria and produce a human-readable DIAGNOSIS.

Responsibility
--------------
Turn a raw episode trace into (a) pass/fail per criterion and (b) one English
sentence explaining *why* it failed.

Inputs:  an :class:`~simkit.runner.EpisodeResult` trace, the ``task.success``
         criteria from ``robotci.yaml``.
Outputs: criterion results and a ``diagnosis`` string.

Why the diagnosis string matters so much
----------------------------------------
It is the handoff point between the deterministic half of the system and the
agent half. The Investigator's entire starting position is this sentence, and
clustering groups on it. A diagnosis of ``"criterion object_in_bin failed"``
tells an agent nothing and produces a bad investigation; ``"Gripper closed at
t=2.0s while still 40mm from the cube; the cube never left the table"`` tells it
where to look.

So diagnoses must be **measured, not guessed**: every number in the sentence
comes from the trace. This module is the one place in the system that is allowed
to explain a failure without an LLM, and that is precisely why it is trustworthy.
"""

from __future__ import annotations

from typing import Any


def evaluate(
    result: Any,
    criteria: list[dict[str, Any]],
    scene: Any = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Score every criterion. Returns ``(criterion_results, diagnosis)``.

    ``diagnosis`` is ``None`` when everything passed.
    """
    raise NotImplementedError
    # TODO(build): dispatch each criterion id to its evaluator, collect
    # results, call diagnose() on the failures.


# -- individual criteria ---------------------------------------------------- #
# Each returns (passed, measured_value, threshold). Add one per criterion id
# supported in robotci.yaml.


def check_object_in_bin(
    result: Any, scene: Any, **kw: Any
) -> tuple[bool, float, float]:
    """Is the target object inside the goal volume at episode end?"""
    raise NotImplementedError
    # TODO(build): final object pose vs bin AABB from scene.handles.


def check_no_collision(result: Any, scene: Any, max_force_n: float = 40.0):
    """Did any contact exceed the force threshold?"""
    raise NotImplementedError
    # TODO(build): max over trace contact forces, excluding intended
    # gripper-object and object-table contacts.


def check_within_time(result: Any, scene: Any, limit_s: float = 12.0):
    """Did the task complete inside the simulated time budget?"""
    raise NotImplementedError
    # TODO(build): compare result.sim_time_s at success against limit.


def check_joint_limits(result: Any, scene: Any, margin: float = 0.95):
    """Did any joint exceed ``margin`` of its position or velocity limit?

    The criterion that most often catches code that would damage real hardware —
    worth reporting the offending joint by name, not index.
    """
    raise NotImplementedError
    # TODO(build): compare trace qpos/qvel against model jnt_range and
    # actuator limits; return the worst joint.


# -- diagnosis -------------------------------------------------------------- #


def diagnose(
    failed: list[dict[str, Any]],
    result: Any,
    scene: Any = None,
) -> str:
    """Compose the failure sentence from measured trace values.

    Rules:
      * Lead with the *earliest* thing that went wrong, not the last criterion
        checked — a time-limit failure caused by an earlier missed grasp should
        read as a missed grasp.
      * Include the numbers that localise the failure in time and space.
      * One or two sentences. This is spliced into a prompt; it is not a log.
    """
    raise NotImplementedError
    # TODO(build): order failures by first-offending timestep, template a
    # sentence per criterion id, join.


# TODO(build): add a `criteria registry` so robotci.yaml can name a criterion
# the customer defined in their own repo, not just the built-ins above.
