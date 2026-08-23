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

import contextlib
import hashlib
import importlib.util
import os
import random
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import mujoco
import numpy as np

from simkit import scene as scene_mod
from simkit import scoring
from simkit.live import LiveFrameWriter

#: Control rate used when ``robotci.yaml`` does not say.
DEFAULT_RATE_HZ = 100
#: Simulated-time budget used when the task declares no ``within_time``.
DEFAULT_SIM_LIMIT_S = 12.0
#: Parent and in-worker guards allow a healthy episode generous realtime slack.
DEFAULT_SCENARIO_TIMEOUT_S = 60.0


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
    error_kind: str | None = None  # timeout | infra | None
    retries: int = 0
    retry_reason: str | None = None
    live_frame_path: str | None = None
    worker_id: str | None = None


class WatchdogExpired(RuntimeError):
    """The wall-clock guard fired: our problem, reported as ``error``."""


class SimTimeExhausted(RuntimeError):
    """The simulated-time budget ran out — a normal end of episode."""


class HarnessError(RuntimeError):
    """The harness could not be imported or does not expose ``run_episode``."""


def run_scenario(
    *,
    scenario_id: str,
    model_path: str,
    harness_path: str,
    params: dict[str, Any],
    seed: int,
    task: dict[str, Any],
    record: bool = False,
    max_wall_s: float = DEFAULT_SCENARIO_TIMEOUT_S,
    live: bool = False,
    live_frame_path: str | None = None,
    progress_path: str | None = None,
    repo_dir: str | None = None,
    on_observe: Callable[[dict[str, Any]], None] | None = None,
    observe_hz: float = 2.0,
    worker_id: str | None = None,
) -> EpisodeResult:
    """Execute one scenario end to end.

    ``record`` turns video on; pass a path string to choose the output file.

    Deterministic: the same arguments always produce the same result. Any source
    of nondeterminism introduced here (thread scheduling, unseeded RNG, wall
    clock leaking into control) breaks reproducibility for every agent above.
    """
    started = time.perf_counter()
    seed = int(seed)
    # Seed every global RNG a controller might reach for. Determinism is the
    # whole product here, so nothing is left to chance.
    random.seed(seed)
    np.random.seed(seed & 0xFFFFFFFF)

    result = EpisodeResult(
        scenario_id=scenario_id,
        seed=seed,
        status="error",
        worker_id=worker_id,
    )
    task = task or {}
    criteria = list(task.get("success") or [])
    rate_hz = int(task.get("rate_hz") or DEFAULT_RATE_HZ)
    sim_limit = _sim_limit(criteria)

    recorder = None
    live_writer = (
        LiveFrameWriter(
            scenario_id,
            destination=live_frame_path,
            progress_path=progress_path,
        )
        if live or live_frame_path is not None
        else None
    )
    scene = None
    try:
        if repo_dir:
            _expose_repo(repo_dir)
        harness = load_harness(harness_path)
        scene = scene_mod.build(
            scene_mod.SceneSpec(
                robot_model_path=model_path,
                task_name=str(task.get("name") or "pick_and_place"),
                params={**(params or {}), "seed": seed},
                include_visuals=bool(record),
            )
        )
        if record:
            from simkit.recorder import Recorder

            recorder = Recorder(fps=min(30, rate_hz))
            # `record` may name the output file directly (the record CLI and
            # recorder.record_scenario do); otherwise it lands in ARTIFACTS_DIR.
            video_path = (
                Path(record)
                if isinstance(record, str)
                else Path(os.environ.get("ARTIFACTS_DIR", "artifacts"))
                / f"{scenario_id}.mp4"
            )

        trace = new_trace(scene, rate_hz=rate_hz, sim_limit_s=sim_limit)
        episode = _EpisodeLoop(
            scene=scene,
            trace=trace,
            rate_hz=rate_hz,
            sim_limit_s=sim_limit,
            deadline=started + float(max_wall_s),
            recorder=recorder,
            live_writer=live_writer,
            on_observe=on_observe,
            observe_hz=observe_hz,
            scenario_id=scenario_id,
            worker_id=worker_id,
            seed=seed,
        )
        harness_params = {
            **(params or {}),
            "seed": seed,
            "rate_hz": rate_hz,
            "control_dt": 1.0 / rate_hz,
            "max_sim_time_s": sim_limit,
            "rng": np.random.default_rng(seed & 0xFFFFFFFF),
            "handles": dict(scene.handles),
            # A harness written by the harness_builder agent drives the sim
            # through these: `step` advances one control period and records the
            # trace, `on_step` is for a harness that steps MuJoCo itself.
            "step": episode.step,
            "on_step": episode.on_step,
        }

        with _wall_clock_guard(max_wall_s):
            try:
                harness(scene.model, scene.data, harness_params)
            except SimTimeExhausted:
                pass
        episode.finish()

        result.sim_time_s = float(scene.data.time)
        result.trace = trace
        result.criteria, result.diagnosis = scoring.evaluate(result, criteria, scene)
        failed = [c for c in result.criteria if not c.get("passed", False)]
        result.status = "failed" if failed else "passed"
        if recorder is not None:
            recorder.overlay(f"seed {seed}  ·  {result.status.upper()}")
            recorder.capture(scene)
            result.video_path = recorder.save(str(video_path))

    except WatchdogExpired as exc:
        result.status = "error"
        result.error_kind = "timeout"
        result.error = str(exc)
        # Score whatever the episode did produce: clustering and the agents
        # need per-criterion evidence even when the wall clock cut it short.
        if scene is not None:
            with contextlib.suppress(Exception):  # partial evidence, best-effort
                episode.finish()
                result.sim_time_s = float(scene.data.time)
                result.trace = trace
                result.criteria, result.diagnosis = scoring.evaluate(
                    result, criteria, scene
                )
    except HarnessError as exc:
        result.status = "error"
        result.error_kind = "infra"
        result.error = f"harness: {exc}"
    except scene_mod.SceneError as exc:
        result.status = "error"
        result.error_kind = "infra"
        result.error = f"scene: {exc}"
    except Exception as exc:  # noqa: BLE001 - any crash is our error, not theirs
        result.status = "error"
        result.error_kind = "infra"
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        if recorder is not None:
            recorder.close()
        if live_writer is not None:
            live_writer.close()
        result.duration_s = round(time.perf_counter() - started, 4)
    return result


def _expose_repo(repo_dir: str) -> None:
    """Point the harness at the checkout under test.

    Cluster verification runs execute against a patched worktree, not the base
    clone; the harness imports the customer's code from ``ROBOTCI_REPO_DIR``
    (or a plain import via ``sys.path``), so both must name that worktree.
    """
    resolved = str(Path(repo_dir).resolve())
    os.environ["ROBOTCI_REPO_DIR"] = resolved
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    # The customer's modules may already be imported from another checkout in
    # this worker process; evict them so the harness re-imports from the
    # requested worktree.
    for name, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            in_repo = Path(module_file).resolve().is_relative_to(resolved)
        except (OSError, ValueError):
            continue
        if not in_repo and _under_any_repo_checkout(module_file):
            del sys.modules[name]


def _under_any_repo_checkout(module_file: str) -> bool:
    """Whether the module was imported from a workspace checkout/worktree."""
    parts = Path(module_file).resolve().parts
    return "workspaces" in parts


def load_harness(harness_path: str) -> Any:
    """Import the agent-written harness module and return its ``run_episode``.

    Import failure is an ``error``, never a ``failure`` — the customer's robot
    is not at fault when our generated harness does not import.
    """
    path = Path(harness_path).resolve()
    if not path.is_file():
        raise HarnessError(f"harness module not found: {path}")
    digest = hashlib.blake2b(str(path).encode(), digest_size=6).hexdigest()
    module_name = f"robotci_harness_{path.stem}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise HarnessError(f"cannot load harness from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    parent = str(path.parent)
    inserted = parent not in sys.path
    if inserted:
        sys.path.insert(0, parent)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise HarnessError(f"{path.name} failed to import: {exc}") from exc
    finally:
        if inserted and parent in sys.path:
            sys.path.remove(parent)
    run_episode = getattr(module, "run_episode", None)
    if not callable(run_episode):
        raise HarnessError(f"{path.name} does not expose a callable run_episode")
    return run_episode


def new_trace(scene: Any, *, rate_hz: int, sim_limit_s: float) -> dict[str, Any]:
    """Preallocate the per-step buffers ``collect_trace`` appends into."""
    model = scene.model
    capacity = int(rate_hz * sim_limit_s) + 2
    joint_ids = scene_mod.robot_joint_ids(scene)
    names = [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, jid) or f"joint{jid}"
        for jid in joint_ids
    ]
    return {
        "n": 0,
        "capacity": capacity,
        "rate_hz": rate_hz,
        "dt": float(model.opt.timestep),
        "joint_ids": joint_ids,
        "joint_names": names,
        "joint_qposadr": [int(model.jnt_qposadr[j]) for j in joint_ids],
        "joint_dofadr": [int(model.jnt_dofadr[j]) for j in joint_ids],
        "joint_range": np.asarray(
            [model.jnt_range[j].copy() for j in joint_ids], dtype=float
        ).reshape(-1, 2),
        "joint_limited": [bool(model.jnt_limited[j]) for j in joint_ids],
        "joint_type": [int(model.jnt_type[j]) for j in joint_ids],
        "t": np.zeros(capacity, dtype=float),
        "qpos": np.zeros((capacity, len(joint_ids)), dtype=float),
        "qvel": np.zeros((capacity, len(joint_ids)), dtype=float),
        "object_pos": np.zeros((capacity, 3), dtype=float),
        "ee_pos": np.zeros((capacity, 3), dtype=float),
        "gripper_object_dist": np.zeros(capacity, dtype=float),
        "contact_force": np.zeros(capacity, dtype=float),
        "contact_pair": [""] * capacity,
        "truncated": False,
    }


def collect_trace(scene: Any, step: int, trace: dict[str, Any]) -> None:
    """Append one step of state to the trace buffer.

    Keep this cheap — it runs at control rate for every scenario. Store arrays,
    not dicts-per-step.
    """
    capacity = int(trace["capacity"])
    index = int(step)
    if index >= capacity:
        trace["truncated"] = True
        return
    data = scene.data
    qposadr = trace["joint_qposadr"]
    dofadr = trace["joint_dofadr"]

    trace["t"][index] = float(data.time)
    if qposadr:
        trace["qpos"][index] = data.qpos[qposadr]
        trace["qvel"][index] = data.qvel[dofadr]

    obj = scene.handles.get("object")
    object_pos = np.asarray(data.xpos[obj], dtype=float) if obj is not None else None
    if object_pos is not None:
        trace["object_pos"][index] = object_pos

    ee = _ee_position(scene)
    if ee is not None:
        trace["ee_pos"][index] = ee
        if object_pos is not None:
            trace["gripper_object_dist"][index] = float(np.linalg.norm(ee - object_pos))

    force, pair = _worst_unintended_contact(scene)
    trace["contact_force"][index] = force
    trace["contact_pair"][index] = pair
    trace["n"] = max(int(trace["n"]), index + 1)


class _EpisodeLoop:
    """Drives (or supervises) stepping, enforcing both time guards."""

    def __init__(
        self,
        *,
        scene: Any,
        trace: dict[str, Any],
        rate_hz: int,
        sim_limit_s: float,
        deadline: float,
        recorder: Any = None,
        live_writer: LiveFrameWriter | None = None,
        on_observe: Callable[[dict[str, Any]], None] | None = None,
        observe_hz: float = 2.0,
        scenario_id: str = "",
        worker_id: str | None = None,
        seed: int = 0,
    ) -> None:
        self.scene = scene
        self.trace = trace
        self.rate_hz = rate_hz
        self.sim_limit_s = sim_limit_s
        self.deadline = deadline
        self.recorder = recorder
        self.live_writer = live_writer
        self.on_observe = on_observe
        self.observe_hz = float(observe_hz)
        self.scenario_id = scenario_id
        self.worker_id = worker_id
        self.seed = seed
        self.steps = 0
        self._sim_per_control = max(
            1, round((1.0 / rate_hz) / float(scene.model.opt.timestep))
        )
        self._frame_every = max(1, round(rate_hz / 30))
        self._last_observe: float | None = None
        try:
            self._realtime_factor = float(os.environ.get("SIMKIT_REALTIME_FACTOR", "0"))
        except ValueError:
            self._realtime_factor = 0.0
        self._wall_start = time.monotonic()
        collect_trace(scene, 0, trace)
        self._capture(force=True)
        self._observe(force=True)

    def step(self, n: int = 1) -> float:
        """Advance ``n`` control periods, recording the trace. Returns sim time."""
        for _ in range(max(1, int(n))):
            self._guard()
            for _ in range(self._sim_per_control):
                mujoco.mj_step(self.scene.model, self.scene.data)
            self.steps += 1
            collect_trace(self.scene, self.steps, self.trace)
            self._capture()
            self._observe()
            self._pace()
        return float(self.scene.data.time)

    def _pace(self) -> None:
        """Hold the simulation near wall-clock speed when asked to.

        Physics runs far faster than real time -- a 12s episode finishes in
        well under a second -- which leaves the live frame writer with one or
        two frames to publish and nothing for a viewer to watch. Setting
        SIMKIT_REALTIME_FACTOR to 1 makes an episode take about as long as the
        robot would; 2 runs at half speed. Unset or 0 keeps full speed, which
        is what batch suites and tests want.
        """
        if self._realtime_factor <= 0.0:
            return
        target = self._wall_start + (
            float(self.scene.data.time) * self._realtime_factor
        )
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, 0.25))

    def on_step(self) -> float:
        """Record a step a harness took itself, and enforce the guards."""
        self._guard()
        self.steps += 1
        collect_trace(self.scene, self.steps, self.trace)
        self._capture()
        self._observe()
        return float(self.scene.data.time)

    def finish(self) -> None:
        """Record the terminal state and flush the video, if any."""
        collect_trace(self.scene, self.steps, self.trace)
        self._capture(force=True)
        self._observe(force=True)

    def _guard(self) -> None:
        if time.perf_counter() > self.deadline:
            raise WatchdogExpired(
                f"wall-clock watchdog fired after {self.steps} control steps "
                f"({self.scene.data.time:.2f}s simulated); the controller is not "
                "advancing the simulation"
            )
        if float(self.scene.data.time) >= self.sim_limit_s:
            raise SimTimeExhausted(
                f"simulated time budget of {self.sim_limit_s:.2f}s reached"
            )

    def _capture(self, force: bool = False) -> None:
        if self.recorder is not None and (force or not self.steps % self._frame_every):
            self.recorder.overlay(self._caption())
            self.recorder.capture(self.scene)
        if self.live_writer is not None:
            self.live_writer.set_progress(self._progress(), float(self.scene.data.time))
            self.live_writer.maybe_capture(self.scene, force=force)

    def _observe(self, force: bool = False) -> None:
        if self.on_observe is None:
            return
        now = time.monotonic()
        interval = 1.0 / self.observe_hz if self.observe_hz > 0 else float("inf")
        last = self._last_observe
        if not force and last is not None and now - last < interval:
            return
        self._last_observe = now
        progress = self._progress()
        try:
            self.on_observe(
                {
                    "kind": "scenario_progress",
                    "scenario_id": self.scenario_id,
                    "seed": self.seed,
                    "worker_id": self.worker_id,
                    "progress": progress,
                    "sim_time_s": float(self.scene.data.time),
                    "live_frame_path": (
                        self.live_writer.rel_path
                        if self.live_writer is not None and self.live_writer.has_frame
                        else None
                    ),
                }
            )
        except Exception:  # noqa: BLE001 - observer is an optional side channel
            return

    def _progress(self) -> float | None:
        if self.sim_limit_s <= 0:
            return None
        return min(max(float(self.scene.data.time) / self.sim_limit_s, 0.0), 1.0)

    def _caption(self) -> str:
        params = self.scene.spec.params or {}
        bits = [f"seed {self.seed}", f"t={float(self.scene.data.time):.2f}s"]
        for key in ("friction", "object_mass_kg", "latency_steps"):
            if key in params:
                bits.append(f"{key} {params[key]}")
        return "  ·  ".join(bits)


def _sim_limit(criteria: list[dict[str, Any]]) -> float:
    for criterion in criteria:
        if criterion.get("id") == "within_time":
            return float(criterion.get("limit_s", DEFAULT_SIM_LIMIT_S))
    return DEFAULT_SIM_LIMIT_S


def _ee_position(scene: Any) -> np.ndarray | None:
    data = scene.data
    site = scene.handles.get("gripper_site")
    if site is not None:
        return np.asarray(data.site_xpos[site], dtype=float)
    body = scene.handles.get("ee_body")
    if body is not None:
        return np.asarray(data.xpos[body], dtype=float)
    return None


def _worst_unintended_contact(scene: Any) -> tuple[float, str]:
    """Peak contact force this step, ignoring the contacts the task requires.

    Gripper-on-object and object-on-table contacts are how a pick-and-place is
    supposed to work; counting them as collisions would fail every passing run.
    """
    model, data = scene.model, scene.data
    if data.ncon == 0:
        return 0.0, ""
    intended = _intended_pairs(scene)
    worst = 0.0
    worst_pair = ""
    buffer = np.zeros(6, dtype=float)
    for i in range(data.ncon):
        contact = data.contact[i]
        g1, g2 = int(contact.geom1), int(contact.geom2)
        if (g1, g2) in intended or (g2, g1) in intended:
            continue
        mujoco.mj_contactForce(model, data, i, buffer)
        magnitude = float(np.linalg.norm(buffer[:3]))
        if magnitude > worst:
            worst = magnitude
            worst_pair = f"{_geom_name(model, g1)}/{_geom_name(model, g2)}"
    return worst, worst_pair


def _intended_pairs(scene: Any) -> set[tuple[int, int]]:
    cached = getattr(scene, "_intended_pairs_cache", None)
    if cached is not None:
        return cached
    pairs = _compute_intended_pairs(scene)
    scene._intended_pairs_cache = pairs
    return pairs


def _compute_intended_pairs(scene: Any) -> set[tuple[int, int]]:
    model = scene.model
    obj = scene.handles.get("object_geom")
    table = scene.handles.get("table_geom")
    floor = scene.handles.get("floor_geom")
    pairs: set[tuple[int, int]] = set()
    if obj is None:
        return pairs
    for other in (table, floor):
        if other is not None:
            pairs.add((obj, other))
    ee_body = scene.handles.get("ee_body")
    if ee_body is not None:
        for gid in range(model.ngeom):
            body = int(model.geom_bodyid[gid])
            # Anything on the end-effector body or below it is the gripper.
            hops = 0
            while body > 0 and hops < 64:
                if body == ee_body:
                    pairs.add((obj, gid))
                    break
                body = int(model.body_parentid[body])
                hops += 1
    return pairs


def _geom_name(model: Any, gid: int) -> str:
    return mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, gid) or f"geom{gid}"


class _wall_clock_guard:
    """Hard wall-clock stop for a controller that never yields to our hooks.

    ``SIGALRM`` only works on the main thread of a POSIX process, which is where
    pool workers run; elsewhere the guards inside ``_EpisodeLoop`` are the only
    protection, so this degrades quietly rather than failing the run.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = max(0.1, float(seconds))
        self._previous = None
        self._armed = False

    def __enter__(self) -> Self:
        if (
            hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        ):
            self._previous = signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
            self._armed = True
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._armed:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            if self._previous is not None:
                signal.signal(signal.SIGALRM, self._previous)
        return False

    def _fire(self, *_: Any) -> None:
        raise WatchdogExpired(
            f"wall-clock watchdog fired after {self.seconds:.1f}s of real time "
            "inside the harness"
        )
