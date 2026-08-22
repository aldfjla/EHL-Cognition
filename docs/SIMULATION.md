# Simulation — the oracle

`packages/simkit` is the half of the system that does not guess. Two invariants
hold throughout:

1. **Determinism.** `(model, harness, seed)` always produces the same result.
2. **No agent calls.** Nothing in `simkit` imports `orchestrator`.

Determinism is not a nice property here, it is load-bearing. An Investigator
can only debug a failure it can re-run; a before/after video pair only proves
anything if both sides ran the identical world. Every design decision below
follows from it.

## Model resolution

Library first, always. Resolution order:

1. `robotci.yaml` names `robot.menagerie` → use it.
2. `robotci.yaml` names `robot.model_path` → use it.
3. Automatic identification against the local Menagerie index → use it.
4. Nothing matched → the **Modeler agent** synthesizes MJCF.

Steps 1–3 cost no agent time and produce a physically curated model. Step 4 is
the more impressive demo and the worse engineering outcome, which is why it is
genuinely last rather than nominally last.

### Identification signals

In descending reliability, implemented in `models/resolver.py`:

1. An explicit config entry.
2. A URDF/xacro in the repo — joint counts and link names usually name the
   vendor outright.
3. Driver imports and package names (`franka`, `ur_rtde`, `pymycobot`).
4. Joint limit tables and DH/calibration constants — a 7-DOF arm with a
   specific limit set is identifiable even when nothing is named.

### The Menagerie index

`vendor/menagerie` is a few hundred MB of XML. `models/menagerie.py` builds a
small `index.json` over it: name, vendor, DOF, kind, main MJCF path. The index
is small enough to paste into the Modeler's prompt, which is what lets the agent
*name* a model rather than clone a library to go looking.

### Generated models are guesses, and say so

A synthesized model is a guess about physics, and every conclusion drawn against
it inherits that guess. So `models/generator.py`:

* records every inferred quantity (mass, inertia, damping, friction) in
  `assumptions`;
* surfaces each as a `constraint` finding on the blackboard;
* reports low `confidence`, and the report states the model was generated.

A failure found against a generated model is a **lead, not a verdict.** Saying
that plainly is worth more than a system that presents guesses as facts.

Every model — library or generated — passes `generator.validate()` before use:
it must compile, survive a second of passive simulation without exploding, and
hold a pose against gravity under PD control. An arm that cannot hold itself up
fails every scenario for reasons unrelated to the code under test.

## Scene composition

`scene.py` assembles robot + task world. The robot MJCF is **included, never
edited** — Menagerie models are curated and rewriting their XML is how physical
fidelity silently degrades. Task geometry (table, object, bin, obstacles) is a
separate MJCF that `<include>`s the robot.

Per-scenario parameters are applied to the *compiled* model where MuJoCo allows
it — masses, frictions, positions — rather than by string-templating XML.
Anything that cannot be applied post-compile must raise, not be skipped: a
scenario that silently failed to randomize is a false pass.

## Scenarios

The QA Lead agent picks the **axes and ranges**. `scenarios.py` derives every
concrete world from a single integer:

```
scenario_seed = derive_seed(base_seed, index)
```

`(base_seed, index)` fully determines a scenario. Replaying one needs no stored
state — which is why `simkit run --seed N` is enough to reproduce anything the
suite found, and why that command appears in the Investigator's prompt.

Sampling is **stratified**, not uniform. With ~24 samples across several axes,
uniform random draws leave visible gaps, and a gap is a bug that ships.

Scenario 0 is the **nominal case** — the midpoint of every axis — so a total
failure is distinguishable from an edge-case failure at a glance.

Typical axes for a pick-and-place arm:

| Axis | Why it finds bugs |
|---|---|
| object position | tests the controller's reach and IK edge cases |
| object mass | grip force is usually a hardcoded constant |
| friction | changes approach time, breaks fixed-timer logic |
| sensor noise | tests whether the controller filters or trusts raw reads |
| actuation latency | tests assumptions about instantaneous response |

## Running one scenario

`runner.py` builds the scene, hands control to the harness, and steps to a
termination condition.

The customer's control code runs **in the worker process**, which means their
infinite loop is our infinite loop. Two guards, both mandatory:

* a simulated-time limit from `task.success.within_time`;
* a **wall-clock watchdog**, because a controller can burn real seconds without
  advancing simulated time at all.

Tripping the wall-clock guard yields `status: "error"`, not `"failed"`. The
distinction runs through the whole system: an error is our problem, a failure is
theirs.

## Scoring, and the diagnosis string

`scoring.py` evaluates each criterion and — when something failed — writes one
English sentence explaining why.

**That sentence is the hinge of the entire system.** It is where the
deterministic half hands off to the agent half. The Investigator's whole
starting position is this string, and `clustering.py` groups on it.

Compare:

> ❌ `criterion object_in_bin failed`
>
> ✅ `Gripper closed at t=2.0s while still 40mm from the cube; the cube never
> left the table. Approach was still in progress (joint 5 velocity 0.8 rad/s).`

The first produces a bad investigation. The second tells an agent where to look.

Rules for `diagnose()`:

* **Lead with the earliest failure**, not the last criterion checked. A
  time-limit failure caused by a missed grasp should read as a missed grasp.
* **Every number comes from the trace.** Measured, never inferred. This module
  is the one place allowed to explain a failure without an LLM, and that is
  precisely why it is trustworthy.
* **One or two sentences.** It is spliced into a prompt; it is not a log.

### Built-in criteria

| id | checks |
|---|---|
| `object_in_bin` | final object pose inside the goal volume |
| `no_collision` | peak contact force below threshold, excluding intended contacts |
| `within_time` | task completed inside the simulated time budget |
| `joint_limits_respected` | no joint past `margin` of its position/velocity limit |

`joint_limits_respected` is the one that most often catches code that would
damage real hardware, so it reports the offending joint **by name**, not index.

## The suite

`suite.py` runs the matrix in a `ProcessPoolExecutor` — processes, not threads,
because MuJoCo releases the GIL unevenly and the customer's control code is
arbitrary Python. Processes also contain a crash: a segfault in one scenario
must not take the suite with it.

Results arrive out of order and are **re-sorted by scenario index** before
returning. A suite whose output order depends on scheduling produces different
clusters run to run, which would make the system look flaky when it is not.

`summarize()` counts `errored` separately from `failed`, everywhere it surfaces.

`compare(before, after)` is the VERIFY gate's core question, returning three
buckets: fixed, still failing, and **newly broken**. The third is the one that
matters — a patch that trades one failure for another must be caught here, not
by the customer.

## Video

`recorder.py` renders offscreen frames to mp4.

There is no system ffmpeg on the target machine, by constraint. `imageio-ffmpeg`
ships its own static binary; resolve it via `imageio_ffmpeg.get_ffmpeg_exe()`
and never shell out to a bare `ffmpeg`.

Offscreen rendering dominates suite wall-clock time, so `policy.record_video`
defaults to `failures`: run the suite headless, then re-run only the failing
seeds with recording on. Re-running costs nothing in correctness because seeds
are deterministic — the recorded episode is the same episode.

Frames carry a burnt-in caption (`seed 4471 · friction 0.42 · FAILED at t=2.4s`).
An unlabelled clip of a robot arm is much harder to read than a labelled one,
and these clips end up in a PR body where nobody has context.

The **before/after pair** is the closing argument of the demo: identical seed,
identical world, robot failing on the left and succeeding on the right, with
nothing between them but an agent's patch. The before clip is the failing
episode; the after clip is a separate post-fix simkit run of that same seed,
written under a distinct artifact path. Same seed on both sides or the
comparison means nothing. If the post-fix run fails or recording is unavailable,
the report says that no verified after-video exists instead of reusing the
failure clip.

## The CLI is the agent-facing API

`simkit run`, `simkit suite`, `simkit models`, `simkit record`.

This is not a convenience wrapper. Devin sessions reproduce failures by invoking
these commands, and every role prompt references them. Consequences:

* every command must be reproducible from flags alone — no hidden state;
* human-readable by default, `--json` for machine consumption (agents parse the
  JSON, judges read the table);
* non-zero exit on scenario failure, so it composes as a real CI step.
