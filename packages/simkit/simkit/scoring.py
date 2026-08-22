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

from collections.abc import Callable
from typing import Any

import numpy as np

#: Height above the bin floor that still counts as "in the bin", metres.
BIN_DEPTH_TOLERANCE = 0.12
#: Joints with less travel than this are grippers, whose travel stops are the
#: normal way to hold something rather than a limit violation.
GRIPPER_TRAVEL_SPAN = 0.2
#: Joint speed treated as "would damage real hardware", rad/s.
DEFAULT_MAX_JOINT_VEL = 8.0


def evaluate(
    result: Any,
    criteria: list[dict[str, Any]],
    scene: Any = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Score every criterion. Returns ``(criterion_results, diagnosis)``.

    ``diagnosis`` is ``None`` when everything passed.
    """
    outcomes: list[dict[str, Any]] = []
    for criterion in criteria or []:
        cid = str(criterion.get("id") or "")
        checker = CRITERIA.get(cid)
        options = {
            k: v for k, v in criterion.items() if k not in {"id", "label", "weight"}
        }
        if checker is None:
            outcomes.append(
                {
                    "id": cid,
                    "passed": False,
                    "measured": None,
                    "threshold": None,
                    "detail": f"unknown criterion id {cid!r}; nothing was checked",
                }
            )
            continue
        passed, measured, threshold = checker(result, scene, **options)
        outcomes.append(
            {
                "id": cid,
                "passed": bool(passed),
                "measured": _round(measured),
                "threshold": _round(threshold),
            }
        )

    failed = [o for o in outcomes if not o["passed"]]
    diagnosis = diagnose(failed, result, scene) if failed else None
    return outcomes, diagnosis


# -- individual criteria ---------------------------------------------------- #
# Each returns (passed, measured_value, threshold). Add one per criterion id
# supported in robotci.yaml.


def check_object_in_bin(
    result: Any, scene: Any, **kw: Any
) -> tuple[bool, float, float]:
    """Is the target object inside the goal volume at episode end?"""
    tolerance = float(kw.get("tolerance_m", 0.0) or 0.0)
    final = _final_object_pos(result)
    target = _bin_position(scene)
    if final is None or target is None:
        return False, float("inf"), 0.0
    half = _bin_half_extent(scene)
    horizontal = float(np.linalg.norm(final[:2] - target[:2]))
    limit = float(np.max(half[:2])) + tolerance
    inside_height = target[2] - 0.02 <= final[2] <= target[2] + BIN_DEPTH_TOLERANCE
    return bool(horizontal <= limit and inside_height), horizontal, limit


def check_no_collision(result: Any, scene: Any, max_force_n: float = 40.0):
    """Did any contact exceed the force threshold?"""
    forces = _array(result, "contact_force")
    peak = float(np.max(forces)) if forces.size else 0.0
    return bool(peak <= float(max_force_n)), peak, float(max_force_n)


def check_within_time(result: Any, scene: Any, limit_s: float = 12.0):
    """Did the task complete inside the simulated time budget?"""
    completed_at = _completion_time(result, scene)
    limit = float(limit_s)
    if completed_at is None:
        return False, float(getattr(result, "sim_time_s", 0.0) or 0.0), limit
    return bool(completed_at <= limit), float(completed_at), limit


def check_joint_limits(result: Any, scene: Any, margin: float = 0.95, **kw: Any):
    """Did any joint exceed ``margin`` of its position or velocity limit?

    The criterion that most often catches code that would damage real hardware —
    worth reporting the offending joint by name, not index.
    """
    max_vel = float(kw.get("max_vel_rad_s", DEFAULT_MAX_JOINT_VEL))
    worst = 0.0
    for usage in _joint_usage(result, max_vel=max_vel):
        worst = max(worst, usage["usage"])
    return bool(worst <= float(margin)), worst, float(margin)


def _joint_usage(
    result: Any, max_vel: float = DEFAULT_MAX_JOINT_VEL
) -> list[dict[str, Any]]:
    """Per-joint peak fraction of its position or velocity limit, measured."""
    trace = getattr(result, "trace", {}) or {}
    qpos = _array(result, "qpos")
    qvel = _array(result, "qvel")
    times = _array(result, "t")
    ranges = np.asarray(trace.get("joint_range", np.zeros((0, 2))), dtype=float)
    limited = list(trace.get("joint_limited") or [])
    names = list(trace.get("joint_names") or [])
    if qpos.ndim != 2 or qpos.size == 0:
        return []

    out: list[dict[str, Any]] = []
    for j in range(qpos.shape[1]):
        name = names[j] if j < len(names) else f"joint{j}"
        best = {
            "joint": name,
            "usage": 0.0,
            "kind": "position",
            "value": 0.0,
            "limit": 0.0,
            "t": 0.0,
            "step": 0,
        }
        if j < ranges.shape[0] and (j >= len(limited) or limited[j]):
            low, high = float(ranges[j, 0]), float(ranges[j, 1])
            span = high - low
            if span > GRIPPER_TRAVEL_SPAN:
                centre = (high + low) / 2.0
                # 0 at the centre of travel, 1 at either hard stop.
                usage = np.abs(qpos[:, j] - centre) / (span / 2.0)
                step = int(np.argmax(usage))
                value = float(qpos[step, j])
                best = {
                    "joint": name,
                    "usage": float(usage[step]),
                    "kind": "position",
                    "value": value,
                    "limit": high if value > centre else low,
                    "t": float(times[step]) if step < times.size else 0.0,
                    "step": step,
                }
        if qvel.ndim == 2 and j < qvel.shape[1] and max_vel > 0:
            speeds = np.abs(qvel[:, j]) / max_vel
            step = int(np.argmax(speeds))
            if float(speeds[step]) > best["usage"]:
                best = {
                    "joint": name,
                    "usage": float(speeds[step]),
                    "kind": "velocity",
                    "value": float(qvel[step, j]),
                    "limit": max_vel,
                    "t": float(times[step]) if step < times.size else 0.0,
                    "step": step,
                }
        out.append(best)
    return out


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
    if not failed:
        return ""
    ordered = sorted(
        failed,
        key=lambda outcome: _first_offence_time(outcome, result, scene),
    )
    sentences = [_sentence(outcome, result, scene) for outcome in ordered[:2]]
    return " ".join(s for s in sentences if s)


def register_criterion(
    cid: str,
    checker: Callable[..., tuple[bool, Any, Any]],
) -> None:
    """Add a criterion so ``robotci.yaml`` can name one the customer defined."""
    CRITERIA[str(cid)] = checker


CRITERIA: dict[str, Callable[..., tuple[bool, Any, Any]]] = {
    "object_in_bin": check_object_in_bin,
    "no_collision": check_no_collision,
    "within_time": check_within_time,
    "joint_limits_respected": check_joint_limits,
}


def _sentence(outcome: dict[str, Any], result: Any, scene: Any) -> str:
    cid = outcome.get("id")
    if cid == "object_in_bin":
        return _object_sentence(outcome, result, scene)
    if cid == "no_collision":
        return _collision_sentence(outcome, result)
    if cid == "joint_limits_respected":
        return _joint_sentence(outcome, result)
    if cid == "within_time":
        measured = outcome.get("measured")
        threshold = outcome.get("threshold")
        return (
            f"The task was still unfinished when the {threshold}s simulated "
            f"budget ran out at t={measured}s."
        )
    detail = outcome.get("detail") or "no evaluator was available"
    return f"Criterion {cid} could not be scored: {detail}."


def _object_sentence(outcome: dict[str, Any], result: Any, scene: Any) -> str:
    final = _final_object_pos(result)
    start = _first_object_pos(result)
    lifted = 0.0
    if final is not None and start is not None:
        lifted = float(np.max(_array(result, "object_pos")[:, 2]) - start[2])
    distance = outcome.get("measured")
    closest, closest_t = _closest_approach(result)
    if lifted < 0.01:
        moved = (
            float(np.linalg.norm(final[:2] - start[:2]))
            if final is not None and start is not None
            else 0.0
        )
        return (
            f"The object never left the table (peak lift {lifted * 1000:.0f}mm, "
            f"moved {moved * 1000:.0f}mm horizontally); the gripper got no closer "
            f"than {closest * 1000:.0f}mm at t={closest_t:.2f}s."
        )
    return (
        f"The object was lifted {lifted * 1000:.0f}mm but ended {distance}m from "
        f"the bin centre, outside the {outcome.get('threshold')}m goal volume."
    )


def _collision_sentence(outcome: dict[str, Any], result: Any) -> str:
    forces = _array(result, "contact_force")
    times = _array(result, "t")
    if forces.size == 0:
        return "A contact exceeded the force threshold."
    index = int(np.argmax(forces))
    pairs = (getattr(result, "trace", {}) or {}).get("contact_pair") or []
    pair = pairs[index] if index < len(pairs) else ""
    where = f" between {pair}" if pair else ""
    at = float(times[index]) if index < times.size else 0.0
    return (
        f"An unintended contact{where} peaked at {outcome.get('measured')}N at "
        f"t={at:.2f}s, over the {outcome.get('threshold')}N limit."
    )


def _joint_sentence(outcome: dict[str, Any], result: Any) -> str:
    usages = _joint_usage(result)
    if not usages:
        return "A joint went past its safe range."
    worst = max(usages, key=lambda entry: entry["usage"])
    if worst["kind"] == "velocity":
        return (
            f"Joint {worst['joint']} hit {worst['value']:.2f} rad/s at "
            f"t={worst['t']:.2f}s, {worst['usage'] * 100:.0f}% of the "
            f"{worst['limit']:.1f} rad/s safe speed."
        )
    return (
        f"Joint {worst['joint']} reached {worst['value']:.3f} rad at "
        f"t={worst['t']:.2f}s, {worst['usage'] * 100:.0f}% of its travel toward "
        f"the {worst['limit']:.3f} rad hard stop."
    )


def _first_offence_time(outcome: dict[str, Any], result: Any, scene: Any) -> float:
    """When did this criterion first go wrong? Used to order the diagnosis."""
    cid = outcome.get("id")
    times = _array(result, "t")
    if cid == "no_collision":
        forces = _array(result, "contact_force")
        threshold = float(outcome.get("threshold") or 0.0)
        over = np.nonzero(forces > threshold)[0]
        if over.size and over[0] < times.size:
            return float(times[over[0]])
    if cid == "joint_limits_respected":
        margin = float(outcome.get("threshold") or 0.95)
        offenders = [u for u in _joint_usage(result) if u["usage"] > margin]
        if offenders:
            return float(min(u["t"] for u in offenders))
    if cid == "object_in_bin":
        # A missed grasp is the earliest possible cause, so rank it before a
        # time-limit failure that it produced.
        _, closest_t = _closest_approach(result)
        return float(closest_t)
    if cid == "within_time":
        return float(getattr(result, "sim_time_s", 0.0) or 0.0) + 1e6
    return float("inf")


def _closest_approach(result: Any) -> tuple[float, float]:
    distances = _array(result, "gripper_object_dist")
    times = _array(result, "t")
    if distances.size == 0:
        return 0.0, 0.0
    index = int(np.argmin(distances))
    return float(distances[index]), float(times[index] if index < times.size else 0.0)


def _completion_time(result: Any, scene: Any) -> float | None:
    """First simulated time at which the object was inside the bin."""
    positions = _array(result, "object_pos")
    times = _array(result, "t")
    target = _bin_position(scene)
    if positions.size == 0 or target is None:
        return None
    half = _bin_half_extent(scene)
    limit = float(np.max(half[:2]))
    for step in range(positions.shape[0]):
        pos = positions[step]
        if (
            float(np.linalg.norm(pos[:2] - target[:2])) <= limit
            and target[2] - 0.02 <= pos[2] <= target[2] + BIN_DEPTH_TOLERANCE
        ):
            return float(times[step] if step < times.size else 0.0)
    return None


def _array(result: Any, key: str) -> np.ndarray:
    trace = getattr(result, "trace", {}) or {}
    values = trace.get(key)
    if values is None:
        return np.zeros(0)
    count = int(trace.get("n") or 0)
    array = np.asarray(values)
    return array[:count] if count else array[:0]


def _final_object_pos(result: Any) -> np.ndarray | None:
    positions = _array(result, "object_pos")
    return positions[-1] if positions.size else None


def _first_object_pos(result: Any) -> np.ndarray | None:
    positions = _array(result, "object_pos")
    return positions[0] if positions.size else None


def _bin_position(scene: Any) -> np.ndarray | None:
    if scene is None:
        return None
    site = scene.handles.get("bin_site")
    if site is not None:
        return np.asarray(scene.data.site_xpos[site], dtype=float)
    body = scene.handles.get("bin")
    if body is not None:
        return np.asarray(scene.data.xpos[body], dtype=float)
    return None


def _bin_half_extent(scene: Any) -> np.ndarray:
    """Half-extent of the goal volume; the generated bin's size is known."""
    from simkit.scene import BIN_HALF

    return np.asarray(BIN_HALF, dtype=float)


def _round(value: Any) -> Any:
    if isinstance(value, float):
        if value in (float("inf"), float("-inf")):
            return None
        return round(value, 4)
    if isinstance(value, np.floating):
        return round(float(value), 4)
    return value
