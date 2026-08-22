"""Run ONE scenario to completion and return a structured result.

Responsibility
--------------
The atomic unit of testing. Build the scene, hand control to the customer's
harness, step the simulation to a termination condition, and hand the recorded
state to the scorer.

Inputs:  a robot model path, a harness module path, one scenario's params+seed.
Outputs: an :class:`EpisodeResult` — criteria outcomes, a trace, an optional
         video path, and a wall-clock duration.

Isolation
---------
The customer's control code runs in this process by default, which means their
infinite loop is our infinite loop. Two guards are mandatory:

* a simulated-time limit from ``task.success.within_time``, and
* a wall-clock watchdog, because a controller can burn real seconds without
  advancing simulated time at all.

A run that trips the wall-clock guard is ``status="error"``, not ``"failed"`` —
the difference matters, because an error is our problem and a failure is theirs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpisodeResult:
    """Everything one scenario produced. The scorer's input."""

    scenario_id: str
    seed: int
    status: str  # passed | failed | error
    sim_time_s: float = 0.0
    duration_s: float = 0.0
    #: Per-step state history: qpos, qvel, contacts, object pose.
    trace: dict[str, Any] = field(default_factory=dict)
    criteria: list[dict[str, Any]] = field(default_factory=list)
    diagnosis: str | None = None
    video_path: str | None = None
    trace_path: str | None = None
    error: str | None = None


def run_scenario(
    *,
    scenario_id: str,
    model_path: str,
    harness_path: str,
    params: dict[str, Any],
    seed: int,
    task: dict[str, Any],
    record: bool = False,
    max_wall_s: float = 120.0,
) -> EpisodeResult:
    """Execute one scenario end to end.

    Deterministic: the same arguments always produce the same result. Any source
    of nondeterminism introduced here (thread scheduling, unseeded RNG, wall
    clock leaking into control) breaks reproducibility for every agent above.
    """
    raise NotImplementedError
    # TODO(build): build scene, import harness, loop mj_step + harness control
    # at rate_hz, collect trace, enforce both time guards, then score.


def load_harness(harness_path: str) -> Any:
    """Import the agent-written harness module and return its ``run_episode``.

    Import failure is an ``error``, never a ``failure`` — the customer's robot
    is not at fault when our generated harness does not import.
    """
    raise NotImplementedError
    # TODO(build): importlib spec_from_file_location, check for run_episode.


def collect_trace(scene: Any, step: int, trace: dict[str, Any]) -> None:
    """Append one step of state to the trace buffer.

    Keep this cheap — it runs at control rate for every scenario. Store arrays,
    not dicts-per-step.
    """
    raise NotImplementedError
    # TODO(build): append qpos/qvel/contact forces/object pose to preallocated
    # numpy arrays.
