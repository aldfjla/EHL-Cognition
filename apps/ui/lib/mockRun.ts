/**
 * A scripted run, replayed client-side.
 *
 * The dashboard is built and demoed before the pipeline exists, and on stage a
 * dead venue wifi must not mean a dead screen (docs/DEMO_SCRIPT.md fallbacks).
 * This module emits exactly the event shapes documented in
 * docs/EVENT_PROTOCOL.md, in the order and rough timing of a real run, so every
 * component renders against live-looking data with no backend at all.
 *
 * The replay is a *replay* and says so: the run title is prefixed `[REPLAY]`
 * and the page shows a banner. A replay must never be presentable as live.
 */

import type {
  Agent,
  Cluster,
  EventPayloads,
  EventType,
  Finding,
  Message,
  Report,
  Run,
  Scenario,
  TypedRunEvent,
} from "./types";

export const MOCK_RUN_ID = "run_replay_demo";
export const REPLAY_PREFIX = "[REPLAY]";

/** One scripted frame: how long to wait, then what arrives. */
export interface ScriptedEvent {
  /** Delay after the previous frame, in milliseconds. */
  delayMs: number;
  event: TypedRunEvent;
}

const REPO = "tumai/panda-pick-and-place";
const SHA = "9f1c7ad4b2e6d80153ca41f7b9e0d2c6a48f1b73";
const SCENARIO_COUNT = 24;
/** Seeds that fail, split across two root causes. */
const GRIP_FAILURES = [3, 9, 14];
const LATENCY_FAILURES = [17, 21];
const WORKER_POOL = ["worker_01", "worker_02", "worker_03"];
const GRIP_DIAGNOSIS =
  "Gripper closed 41mm before reaching the cube; object never left the table.";
const LATENCY_DIAGNOSIS =
  "Actuator latency pushed the approach 180ms late; wrist clipped the bin lip at 0.019m penetration.";

let clockMs = Date.now();

function tick(ms: number): string {
  clockMs += ms;
  return new Date(clockMs).toISOString();
}

function scenarioLabel(index: number): string {
  const payloads = ["light cube", "medium cube", "heavy cube"];
  const frictions = ["high friction", "nominal friction", "low friction"];
  return `${payloads[index % 3]}, ${frictions[Math.floor(index / 3) % 3]}`;
}

function makeScenario(runId: string, index: number): Scenario {
  const failsGrip = GRIP_FAILURES.includes(index);
  const failsLatency = LATENCY_FAILURES.includes(index);
  return {
    id: `scn_${String(index).padStart(2, "0")}`,
    run_id: runId,
    index,
    seed: 4400 + index * 7,
    label: scenarioLabel(index),
    params: {
      payload_kg: Number((0.2 + (index % 3) * 0.45).toFixed(2)),
      friction: Number((0.9 - (Math.floor(index / 3) % 3) * 0.3).toFixed(2)),
      sensor_noise: Number((0.001 + (index % 4) * 0.0015).toFixed(4)),
      actuator_latency_ms: 5 + (index % 5) * 9,
    },
    status: "pending",
    attempt: 1,
    duration_s: null,
    sim_time_s: null,
    criteria: [],
    diagnosis: null,
    cluster_id: failsGrip ? "cl_grip" : failsLatency ? "cl_latency" : null,
    video_path: null,
    live_frame_path: null,
    worker_id: null,
    progress: null,
    trace_path: null,
    error: null,
  };
}

function finishScenario(scenario: Scenario): Scenario {
  const failsGrip = GRIP_FAILURES.includes(scenario.index);
  const failsLatency = LATENCY_FAILURES.includes(scenario.index);
  const passed = !failsGrip && !failsLatency;
  return {
    ...scenario,
    status: passed ? "passed" : "failed",
    duration_s: Number((3.4 + (scenario.index % 5) * 0.31).toFixed(2)),
    sim_time_s: passed ? 6.2 : 8.0,
    criteria: [
      {
        id: "object_in_bin",
        passed,
        value: passed ? 1 : 0,
        threshold: 1,
      },
      {
        id: "no_collision",
        passed: !failsLatency,
        value: failsLatency ? 0.019 : 0.0,
        threshold: 0.005,
      },
    ],
    diagnosis: failsGrip ? GRIP_DIAGNOSIS : failsLatency ? LATENCY_DIAGNOSIS : null,
    video_path: passed ? null : `${scenario.run_id}/${scenario.id}_before.mp4`,
    trace_path: `${scenario.run_id}/${scenario.id}.trace.json`,
  };
}

function makeAgent(
  runId: string,
  id: string,
  role: Agent["role"],
  title: string,
  task: string,
  extra: Partial<Agent> = {},
): Agent {
  const now = new Date(clockMs).toISOString();
  return {
    id,
    run_id: runId,
    session_id: null,
    session_url: null,
    role,
    title,
    task,
    status: "starting",
    iteration: 0,
    max_iterations: 3,
    cluster_id: null,
    scenario_ids: [],
    parent_agent_id: null,
    finding_ids: [],
    last_activity: null,
    desktop_url: null,
    issue: null,
    step: null,
    created_at: now,
    updated_at: now,
    finished_at: null,
    ...extra,
  };
}

/**
 * Build the scripted event log for a replayed run.
 *
 * Deterministic in structure and ordering — the same call always produces the
 * same events in the same order — but timestamps are anchored to the wall clock
 * at build time so that live agent cards show plausible elapsed times.
 */
export function mockRunScript(runId: string = MOCK_RUN_ID): ScriptedEvent[] {
  clockMs = Date.now();
  const script: ScriptedEvent[] = [];
  let seq = 0;

  const push = <K extends EventType>(
    delayMs: number,
    type: K,
    data: EventPayloads[K],
  ): void => {
    seq += 1;
    script.push({
      delayMs,
      event: {
        id: `evt_${String(seq).padStart(4, "0")}`,
        run_id: runId,
        seq,
        type,
        ts: tick(120),
        data,
      } as TypedRunEvent,
    });
  };

  const run: Run = {
    id: runId,
    stage: "TRIGGERED",
    repo: REPO,
    branch: "main",
    commit_sha: SHA,
    commit_message: `${REPLAY_PREFIX} tighten pick-and-place approach trajectory`,
    pushed_by: "arm-team",
    robot_model: null,
    suite: null,
    pull_request_url: null,
    report_id: null,
    error: null,
    created_at: new Date(clockMs).toISOString(),
    updated_at: new Date(clockMs).toISOString(),
    finished_at: null,
  };

  push(0, "run.created", run);

  // --- RESOLVE_MODEL -------------------------------------------------------
  push(600, "run.stage_changed", { stage: "RESOLVE_MODEL", previous_stage: "TRIGGERED" });
  push(900, "message.sent", {
    id: "msg_0001",
    run_id: runId,
    from_agent_id: null,
    to_agent_id: null,
    from_role: "orchestrator",
    to_role: "broadcast",
    kind: "finding",
    body:
      "Menagerie lookup hit: `franka_emika_panda` (7 DoF). No Modeler needed — " +
      "the vendor-calibrated model loads and holds pose against gravity.",
    refs: [],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);

  // --- BUILD_HARNESS -------------------------------------------------------
  const harness = makeAgent(
    runId,
    "agt_harness",
    "harness_builder",
    "Test Infra — MuJoCo harness",
    "Bind src/controller.py:run to MuJoCo actuators without touching the pushed code.",
  );
  push(700, "run.stage_changed", {
    stage: "BUILD_HARNESS",
    previous_stage: "RESOLVE_MODEL",
  });
  push(200, "agent.created", harness);
  push(500, "agent.status_changed", {
    agent_id: harness.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: harness.id,
    session_id: "sess_harness",
    session_url: "https://app.devin.ai/sessions/a11ce001",
    step: "binding MuJoCo actuators",
  });
  push(700, "agent.activity", {
    agent_id: harness.id,
    text: "Faking driver.set_joint_positions() onto mjData.ctrl; units are radians.",
    ts: new Date(clockMs).toISOString(),
  });
  push(900, "finding.created", {
    id: "fnd_harness_units",
    run_id: runId,
    author_agent_id: harness.id,
    author_role: "harness_builder",
    kind: "constraint",
    summary:
      "Controller assumes a 100Hz control loop and radians; harness clamps to that rate.",
    detail:
      "`src/controller.py:31` sleeps 0.01s per step and sends radians directly. " +
      "The harness drives `mjData.ctrl` at 100Hz to match; any scenario that " +
      "changes actuator latency changes effective loop timing.",
    cluster_id: null,
    scenario_ids: [],
    files: ["src/controller.py"],
    confidence: 0.9,
    status: "proposed",
    superseded_by: null,
    created_at: new Date(clockMs).toISOString(),
  } satisfies Finding);
  push(400, "agent.status_changed", {
    agent_id: harness.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });

  // --- DESIGN_SCENARIOS ----------------------------------------------------
  const designer = makeAgent(
    runId,
    "agt_qa",
    "scenario_designer",
    "QA Lead — randomization ranges",
    "Choose which axes to randomize and how wide, straddling controller boundaries.",
  );
  push(500, "run.stage_changed", {
    stage: "DESIGN_SCENARIOS",
    previous_stage: "BUILD_HARNESS",
  });
  push(200, "agent.created", designer);
  push(400, "agent.status_changed", {
    agent_id: designer.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: designer.id,
    session_id: "sess_qa",
    session_url: "https://app.devin.ai/sessions/a11ce002",
    step: "choosing randomized axes",
  });
  push(600, "message.sent", {
    id: "msg_0002",
    run_id: runId,
    from_agent_id: designer.id,
    to_agent_id: null,
    from_role: "scenario_designer",
    to_role: "broadcast",
    kind: "finding",
    body:
      "Found `GRIP_TIMEOUT = 2.0` at `src/controller.py:88`. Ranging payload " +
      "0.2–1.1kg and friction 0.3–0.9 so the approach straddles that timer.",
    refs: [{ type: "finding", id: "fnd_qa_boundary", label: "controller.py:88" }],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(300, "finding.created", {
    id: "fnd_qa_boundary",
    run_id: runId,
    author_agent_id: designer.id,
    author_role: "scenario_designer",
    kind: "observation",
    summary: "Hardcoded GRIP_TIMEOUT = 2.0 at src/controller.py:88.",
    detail:
      "The grasp phase aborts after a fixed 2.0s regardless of approach " +
      "distance. Low friction and heavy payloads both slow the approach.",
    cluster_id: null,
    scenario_ids: [],
    files: ["src/controller.py"],
    confidence: 0.95,
    status: "proposed",
    superseded_by: null,
    created_at: new Date(clockMs).toISOString(),
  } satisfies Finding);
  push(300, "agent.status_changed", {
    agent_id: designer.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });

  // --- RUN_SUITE -----------------------------------------------------------
  const scenarios = Array.from({ length: SCENARIO_COUNT }, (_, i) =>
    makeScenario(runId, i),
  );
  push(500, "run.stage_changed", {
    stage: "RUN_SUITE",
    previous_stage: "DESIGN_SCENARIOS",
  });
  for (const scenario of scenarios) {
    push(40, "scenario.created", scenario);
  }

  let completed = 0;
  let passed = 0;
  let failed = 0;
  for (const scenario of scenarios) {
    const workerId = WORKER_POOL[scenario.index % WORKER_POOL.length];
    const liveFramePath = `live/${scenario.id}.jpg`;
    const queued = scenarios.length - scenario.index - 1;
    push(40, "worker.pool_changed", {
      workers: WORKER_POOL.length,
      busy: 1,
      queued,
      reason: `${workerId} picked up ${scenario.id}`,
    });
    push(120, "scenario.started", {
      scenario_id: scenario.id,
      worker_id: workerId,
    });
    const inProgress = {
      ...scenario,
      status: "running" as const,
      worker_id: workerId,
      progress: 0,
      sim_time_s: 0,
      live_frame_path: liveFramePath,
    };
    const horizon = finishScenario(inProgress).sim_time_s ?? 6.2;
    for (const progress of [0.25, 0.5, 0.75]) {
      push(80, "scenario.progress", {
        scenario_id: scenario.id,
        progress,
        sim_time_s: Number((horizon * progress).toFixed(2)),
        live_frame_path: liveFramePath,
      });
    }
    const finished = {
      ...finishScenario(inProgress),
      live_frame_path: null,
      progress: 1,
    } satisfies Scenario;
    push(260, "scenario.finished", finished);
    push(40, "worker.pool_changed", {
      workers: WORKER_POOL.length,
      busy: 0,
      queued,
      reason: `${workerId} finished ${scenario.id}`,
    });
    completed += 1;
    if (finished.status === "passed") passed += 1;
    else failed += 1;
    if (completed % 4 === 0 || completed === scenarios.length) {
      push(40, "suite.progress", {
        total: scenarios.length,
        completed,
        passed,
        failed,
      });
    }
    if (finished.video_path) {
      push(20, "artifact.created", {
        kind: "video",
        path: finished.video_path,
        scenario_id: finished.id,
        run_id: runId,
      });
    }
  }

  // --- CLUSTER_FAILURES ----------------------------------------------------
  push(700, "run.stage_changed", {
    stage: "CLUSTER_FAILURES",
    previous_stage: "RUN_SUITE",
  });
  push(500, "message.sent", {
    id: "msg_0003",
    run_id: runId,
    from_agent_id: null,
    to_agent_id: null,
    from_role: "orchestrator",
    to_role: "broadcast",
    kind: "finding",
    body:
      "5 failures grouped into 2 failure clusters by diagnosis and parameter " +
      "correlation: grasp timeout (3) and late approach collision (2), plus " +
      "one observation-only trace cluster.",
    refs: [
      { type: "cluster", id: "cl_grip", label: "grasp timeout ×3" },
      { type: "cluster", id: "cl_latency", label: "late approach ×2" },
      { type: "cluster", id: "cl_observation", label: "trace observations" },
    ],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);

  // --- INVESTIGATE ---------------------------------------------------------
  const inv1 = makeAgent(
    runId,
    "agt_inv_grip",
    "investigator",
    "Debug Eng #1 — grasp timeout cluster",
    "Reproduce seed 4421 and explain why the gripper closes early.",
    {
      cluster_id: "cl_grip",
      scenario_ids: GRIP_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
    },
  );
  const inv2 = makeAgent(
    runId,
    "agt_inv_latency",
    "investigator",
    "Debug Eng #2 — late approach cluster",
    "Reproduce seed 4519 and explain the bin-lip collision.",
    {
      cluster_id: "cl_latency",
      scenario_ids: LATENCY_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
    },
  );
  const inv3 = makeAgent(
    runId,
    "agt_inv_trace",
    "investigator",
    "Debug Eng #3 — trace observations",
    "Collect an observation-only trace for future diagnostics without changing the verdict.",
    {
      cluster_id: "cl_observation",
    },
  );
  push(600, "run.stage_changed", {
    stage: "INVESTIGATE",
    previous_stage: "CLUSTER_FAILURES",
  });
  push(150, "agent.created", inv1);
  push(150, "agent.created", inv2);
  push(150, "agent.created", inv3);
  push(400, "agent.status_changed", {
    agent_id: inv1.id,
    status: "working",
    previous_status: "starting",
  });
  push(120, "agent.status_changed", {
    agent_id: inv2.id,
    status: "working",
    previous_status: "starting",
  });
  push(120, "agent.status_changed", {
    agent_id: inv3.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: inv1.id,
    session_id: "sess_inv_grip",
    session_url: "https://app.devin.ai/sessions/a11ce101",
    desktop_url: "/mock/desktop/index.html?agent=agt_inv_grip",
    issue: GRIP_DIAGNOSIS,
    step: "reading controller.py",
  });
  push(100, "agent.updated", {
    agent_id: inv2.id,
    session_id: "sess_inv_latency",
    session_url: "https://app.devin.ai/sessions/a11ce102",
    desktop_url: "/mock/desktop/index.html?agent=agt_inv_latency",
    issue: LATENCY_DIAGNOSIS,
    step: "reading controller.py",
  });
  push(100, "agent.updated", {
    agent_id: inv3.id,
    session_id: "sess_inv_trace",
    session_url: "https://app.devin.ai/sessions/a11ce103",
    step: "collecting trace metadata",
  });
  push(400, "agent.updated", {
    agent_id: inv1.id,
    step: "reproducing seed 4421",
  });
  push(300, "agent.updated", {
    agent_id: inv2.id,
    step: "reproducing seed 4519",
  });
  push(300, "agent.updated", {
    agent_id: inv3.id,
    step: "waiting on missing trace artifact",
  });
  push(100, "agent.status_changed", {
    agent_id: inv3.id,
    status: "blocked",
    previous_status: "working",
  });
  push(800, "agent.activity", {
    agent_id: inv1.id,
    text: "Reproduced seed 4421. Grasp phase aborts at t=2.00s, 41mm short.",
    ts: new Date(clockMs).toISOString(),
  });
  push(300, "agent.activity", {
    agent_id: inv2.id,
    text: "Replayed seed 4519 with latency 5ms — passes. Latency is the variable.",
    ts: new Date(clockMs).toISOString(),
  });
  push(600, "message.sent", {
    id: "msg_0004",
    run_id: runId,
    from_agent_id: inv1.id,
    to_agent_id: null,
    from_role: "investigator",
    to_role: "broadcast",
    kind: "hypothesis",
    body:
      "`GRIP_TIMEOUT = 2.0` is a fixed timer on a variable-duration approach. " +
      "Halving payload mass makes seed 4421 pass, which is the prediction that " +
      "confirms it.",
    refs: [{ type: "scenario", id: "scn_03", label: "seed 4421" }],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(200, "agent.updated", {
    agent_id: inv1.id,
    step: "writing patch",
  });
  push(200, "agent.updated", {
    agent_id: inv2.id,
    step: "writing patch",
  });
  push(500, "finding.created", {
    id: "fnd_rc_grip",
    run_id: runId,
    author_agent_id: inv1.id,
    author_role: "investigator",
    kind: "root_cause",
    summary:
      "Fixed 2.0s grasp timeout aborts the approach before contact on slow approaches.",
    detail:
      "The grasp phase is timed, not measured. Approach duration scales with " +
      "payload mass and inversely with friction; at 1.1kg / 0.3 friction the " +
      "gripper needs 2.6s. The timer fires at 2.0s and the controller reports " +
      "success it never achieved.",
    cluster_id: "cl_grip",
    scenario_ids: GRIP_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
    files: ["src/controller.py"],
    confidence: 0.88,
    status: "proposed",
    superseded_by: null,
    created_at: new Date(clockMs).toISOString(),
  } satisfies Finding);
  push(400, "message.sent", {
    id: "msg_0005",
    run_id: runId,
    from_agent_id: inv2.id,
    to_agent_id: null,
    from_role: "investigator",
    to_role: "broadcast",
    kind: "finding",
    body:
      "Approach waypoints are emitted open-loop; with 40ms+ actuator latency " +
      "the wrist is 180ms behind its commanded pose and clips the bin lip.",
    refs: [{ type: "scenario", id: "scn_17", label: "seed 4519" }],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(300, "finding.created", {
    id: "fnd_rc_latency",
    run_id: runId,
    author_agent_id: inv2.id,
    author_role: "investigator",
    kind: "root_cause",
    summary:
      "Open-loop waypoint following ignores actuator latency, causing a bin-lip collision.",
    detail:
      "`follow_path()` advances to the next waypoint on a wall clock rather " +
      "than on measured joint error, so latency accumulates across the path.",
    cluster_id: "cl_latency",
    scenario_ids: LATENCY_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
    files: ["src/controller.py", "src/path.py"],
    confidence: 0.81,
    status: "proposed",
    superseded_by: null,
    created_at: new Date(clockMs).toISOString(),
  } satisfies Finding);
  push(300, "agent.status_changed", {
    agent_id: inv1.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });
  push(200, "agent.status_changed", {
    agent_id: inv2.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });

  // --- FIX -----------------------------------------------------------------
  const fix1 = makeAgent(
    runId,
    "agt_fix_grip",
    "fixer",
    "Fix Eng #1 — grasp timeout",
    "Replace the fixed grasp timer with a contact-conditioned wait.",
    { cluster_id: "cl_grip", parent_agent_id: inv1.id, iteration: 1 },
  );
  const fix1Attempt = makeAgent(
    runId,
    "agt_fix_grip_attempt1",
    "fixer",
    "Fix Eng #1 — grasp timeout (attempt 1)",
    "Try a contact-conditioned grasp patch within the iteration budget.",
    { cluster_id: "cl_grip", parent_agent_id: inv1.id },
  );
  const fix2 = makeAgent(
    runId,
    "agt_fix_latency",
    "fixer",
    "Fix Eng #2 — late approach",
    "Gate waypoint advance on measured joint error instead of wall clock.",
    { cluster_id: "cl_latency", parent_agent_id: inv2.id, iteration: 1 },
  );
  push(600, "run.stage_changed", { stage: "FIX", previous_stage: "INVESTIGATE" });
  push(150, "agent.created", fix1);
  push(150, "agent.created", fix1Attempt);
  push(150, "agent.created", fix2);
  push(300, "message.sent", {
    id: "msg_0006",
    run_id: runId,
    from_agent_id: inv1.id,
    to_agent_id: fix1.id,
    from_role: "investigator",
    to_role: "fixer",
    kind: "handoff",
    body:
      "Owning cluster `cl_grip`. Cause: fixed 2.0s grasp timeout. Constraint " +
      "from Test Infra: 100Hz loop, radians — do not change the control rate.",
    refs: [
      { type: "finding", id: "fnd_rc_grip", label: "root cause" },
      { type: "finding", id: "fnd_harness_units", label: "100Hz constraint" },
    ],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(100, "agent.updated", {
    agent_id: fix1Attempt.id,
    session_id: "sess_fix_grip_attempt1",
    session_url: "https://app.devin.ai/sessions/a11ce201",
    issue: GRIP_DIAGNOSIS,
    step: "reading controller.py",
  });
  push(100, "agent.status_changed", {
    agent_id: fix1Attempt.id,
    status: "working",
    previous_status: "starting",
  });
  push(120, "agent.status_changed", {
    agent_id: fix2.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: fix1.id,
    session_id: "sess_fix_grip",
    session_url: "https://app.devin.ai/sessions/a11ce202",
    desktop_url: "/mock/desktop/index.html?agent=agt_fix_grip",
    issue: GRIP_DIAGNOSIS,
    step: "reading controller.py",
  });
  push(100, "agent.updated", {
    agent_id: fix2.id,
    session_id: "sess_fix_latency",
    session_url: "https://app.devin.ai/sessions/a11ce203",
    desktop_url: "/mock/desktop/index.html?agent=agt_fix_latency",
    issue: LATENCY_DIAGNOSIS,
    step: "reading controller.py",
  });
  push(250, "agent.updated", {
    agent_id: fix1Attempt.id,
    iteration: 3,
    step: "iteration cap reached",
  });
  push(100, "agent.status_changed", {
    agent_id: fix1Attempt.id,
    status: "failed",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });
  push(200, "agent.status_changed", {
    agent_id: fix1.id,
    status: "working",
    previous_status: "starting",
  });
  push(150, "agent.updated", {
    agent_id: fix1.id,
    step: "reproducing seed 4421",
  });
  push(150, "agent.updated", {
    agent_id: fix2.id,
    step: "reproducing seed 4519",
  });
  push(700, "agent.activity", {
    agent_id: fix1.id,
    text: "Patched controller.py; seeds 4421/4463/4498 green in worktree.",
    ts: new Date(clockMs).toISOString(),
  });
  push(400, "finding.created", {
    id: "fnd_patch_grip",
    run_id: runId,
    author_agent_id: fix1.id,
    author_role: "fixer",
    kind: "patch",
    summary:
      "Wait for gripper contact force with a 6s ceiling instead of a fixed 2.0s timer.",
    detail:
      "`grasp()` now polls the contact sensor and exits on force > 1.5N, " +
      "failing loudly at 6s rather than reporting a phantom success.",
    cluster_id: "cl_grip",
    scenario_ids: GRIP_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
    files: ["src/controller.py"],
    confidence: 0.92,
    status: "proposed",
    superseded_by: null,
    created_at: new Date(clockMs).toISOString(),
  } satisfies Finding);
  push(400, "message.sent", {
    id: "msg_0007",
    run_id: runId,
    from_agent_id: fix2.id,
    to_agent_id: null,
    from_role: "fixer",
    to_role: "broadcast",
    kind: "finding",
    body:
      "Waypoint advance now gated on |q_measured − q_commanded| < 0.02 rad. " +
      "Both latency seeds green, 6 sampled passing seeds still green.",
    refs: [{ type: "commit", id: "b41f0aa", label: "path.py" }],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(200, "agent.updated", {
    agent_id: fix1.id,
    step: "writing patch",
  });
  push(200, "agent.updated", {
    agent_id: fix2.id,
    step: "writing patch",
  });
  push(300, "agent.status_changed", {
    agent_id: fix1.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });
  push(200, "agent.status_changed", {
    agent_id: fix2.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });

  // --- VERIFY --------------------------------------------------------------
  const reviewer = makeAgent(
    runId,
    "agt_reviewer",
    "reviewer",
    "Tech Lead — merged verification",
    "Re-run the full suite with both patches merged and accept or reject.",
  );
  push(600, "run.stage_changed", { stage: "VERIFY", previous_stage: "FIX" });
  push(150, "agent.created", reviewer);
  push(300, "agent.status_changed", {
    agent_id: reviewer.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: reviewer.id,
    session_id: "sess_reviewer",
    session_url: "https://app.devin.ai/sessions/a11ce301",
    step: "running merged verification",
  });
  for (const index of [...GRIP_FAILURES, ...LATENCY_FAILURES]) {
    const scenario = scenarios[index];
    push(200, "scenario.finished", {
      ...scenario,
      status: "passed",
      attempt: 2,
      duration_s: 4.1,
      sim_time_s: 6.4,
      criteria: [
        { id: "object_in_bin", passed: true, value: 1, threshold: 1 },
        { id: "no_collision", passed: true, value: 0, threshold: 0.005 },
      ],
      diagnosis: null,
      video_path: `${runId}/${scenario.id}_after.mp4`,
      trace_path: `${runId}/${scenario.id}.trace.json`,
    } satisfies Scenario);
    push(30, "artifact.created", {
      kind: "video",
      path: `${runId}/${scenario.id}_after.mp4`,
      scenario_id: scenario.id,
      run_id: runId,
    });
  }
  push(200, "suite.progress", {
    total: SCENARIO_COUNT,
    completed: SCENARIO_COUNT,
    passed: SCENARIO_COUNT,
    failed: 0,
  });
  push(300, "finding.updated", { finding_id: "fnd_rc_grip", status: "confirmed" });
  push(80, "finding.updated", { finding_id: "fnd_rc_latency", status: "confirmed" });
  push(300, "message.sent", {
    id: "msg_0008",
    run_id: runId,
    from_agent_id: reviewer.id,
    to_agent_id: null,
    from_role: "reviewer",
    to_role: "broadcast",
    kind: "verdict",
    body:
      "Ship. 24/24 green with both patches merged. Neither patch special-cases " +
      "a seed or weakens a success criterion.",
    refs: [
      { type: "finding", id: "fnd_patch_grip", label: "patch 1" },
      { type: "finding", id: "fnd_rc_latency", label: "patch 2" },
    ],
    ts: new Date(clockMs).toISOString(),
  } satisfies Message);
  push(200, "agent.status_changed", {
    agent_id: reviewer.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });

  // --- REPORT --------------------------------------------------------------
  const reporter = makeAgent(
    runId,
    "agt_reporter",
    "reporter",
    "Eng Manager — incident report",
    "Write the report used verbatim as the pull request body.",
  );
  push(500, "run.stage_changed", { stage: "REPORT", previous_stage: "VERIFY" });
  push(150, "agent.created", reporter);
  push(250, "agent.status_changed", {
    agent_id: reporter.id,
    status: "working",
    previous_status: "starting",
  });
  push(100, "agent.updated", {
    agent_id: reporter.id,
    session_id: "sess_reporter",
    session_url: "https://app.devin.ai/sessions/a11ce302",
    step: "writing incident report",
  });

  const report: Report = {
    id: "rep_replay_demo",
    run_id: runId,
    verdict: "fixed",
    title: "Fix grasp timeout and latency-blind approach in pick-and-place",
    summary: `${REPLAY_PREFIX} Two independent defects made this controller fail 5 of 24 randomized worlds.

Both were logic errors rather than tuning problems: a **fixed 2.0s grasp timer**
on a variable-duration approach, and **open-loop waypoint following** that
ignores actuator latency. Both patches were verified together against the full
suite — 24/24 green, up from 19/24.`,
    incidents: [
      {
        cluster_id: "cl_grip",
        title: "Grasp aborts before contact on slow approaches",
        affected_scenarios: GRIP_FAILURES.length,
        root_cause:
          "`GRIP_TIMEOUT = 2.0` (`src/controller.py:88`) times the grasp phase " +
          "instead of measuring it. Approach duration scales with payload mass " +
          "and inversely with friction; at 1.1kg / 0.3 friction the approach " +
          "needs 2.6s, so the gripper closes 41mm short and the controller " +
          "reports a success that never happened.",
        resolution:
          "`grasp()` now waits on contact force > 1.5N with a 6s ceiling, and " +
          "raises on timeout rather than continuing. A fixed timer is wrong on " +
          "any robot in any simulator — this is not a sim-only failure.",
        files_changed: ["src/controller.py"],
        before_video: `${runId}/scn_03_before.mp4`,
        after_video: `${runId}/scn_03_after.mp4`,
        status: "fixed",
      },
      {
        cluster_id: "cl_latency",
        title: "Wrist clips the bin lip under actuator latency",
        affected_scenarios: LATENCY_FAILURES.length,
        root_cause:
          "`follow_path()` advances waypoints on a wall clock, so with 40ms+ " +
          "actuator latency the arm trails its commanded pose by ~180ms and " +
          "enters the bin 0.019m below the lip.",
        resolution:
          "Waypoint advance is gated on measured joint error " +
          "(|q_measured − q_commanded| < 0.02 rad), making the path " +
          "latency-agnostic. Six previously-passing seeds re-checked for " +
          "regressions.",
        files_changed: ["src/controller.py", "src/path.py"],
        before_video: `${runId}/scn_17_before.mp4`,
        after_video: `${runId}/scn_17_after.mp4`,
        status: "fixed",
      },
    ],
    diff: `diff --git a/src/controller.py b/src/controller.py
index 3f1a9c2..b41f0aa 100644
--- a/src/controller.py
+++ b/src/controller.py
@@ -85,10 +85,14 @@ class PickAndPlace:
     def grasp(self) -> None:
-        GRIP_TIMEOUT = 2.0
-        started = time.monotonic()
-        while time.monotonic() - started < GRIP_TIMEOUT:
-            self.driver.close_gripper()
-        # assume the object is held
+        GRIP_FORCE_N = 1.5
+        GRIP_CEILING_S = 6.0
+        started = time.monotonic()
+        while self.driver.contact_force() < GRIP_FORCE_N:
+            self.driver.close_gripper()
+            if time.monotonic() - started > GRIP_CEILING_S:
+                raise GraspFailed("no contact force after 6.0s")
diff --git a/src/path.py b/src/path.py
index 7c02e11..d9b3f45 100644
--- a/src/path.py
+++ b/src/path.py
@@ -22,7 +22,9 @@ def follow_path(driver, waypoints, dt=0.01):
     for target in waypoints:
         driver.command(target)
-        time.sleep(dt)
+        while max(abs(m - c) for m, c in zip(driver.measured(), target)) > 0.02:
+            time.sleep(dt)
`,
    before: { total: 24, passed: 19, failed: 5, pass_rate: 0.7917 },
    after: { total: 24, passed: 24, failed: 0, pass_rate: 1 },
    pull_request_url: "https://github.com/tumai/panda-pick-and-place/pull/42",
    markdown_path: `${runId}/report.md`,
    created_at: new Date(clockMs).toISOString(),
  };

  push(900, "report.created", report);
  push(200, "agent.status_changed", {
    agent_id: reporter.id,
    status: "succeeded",
    previous_status: "working",
    finished_at: new Date(clockMs).toISOString(),
  });
  push(400, "run.stage_changed", { stage: "PR_OPENED", previous_stage: "REPORT" });
  push(300, "run.finished", {
    ...run,
    stage: "PR_OPENED",
    robot_model: {
      source: "menagerie",
      name: "franka_emika_panda",
      model_path: "vendor/menagerie/franka_emika_panda/panda.xml",
      dof: 7,
      confidence: 0.97,
      provenance: "Menagerie entry franka_emika_panda; robotci.yaml robot.menagerie",
      license: "Apache License 2.0",
      processing_steps: ["Menagerie lookup"],
      approximate: false,
      cache_hit: true,
    },
    suite: {
      total: 24,
      passed: 24,
      failed: 0,
      pass_rate: 1,
      baseline_pass_rate: 0.7917,
    },
    pull_request_url: report.pull_request_url,
    report_id: report.id,
    updated_at: new Date(clockMs).toISOString(),
    finished_at: new Date(clockMs).toISOString(),
  } satisfies Run);

  return script;
}

/**
 * Fold the scripted agent events into the roster shown after a completed
 * replay. Keeping this projection event-driven prevents the static demo from
 * drifting away from the replay's final state.
 */
export function mockAgents(runId: string = MOCK_RUN_ID): Agent[] {
  const agents = new Map<string, Agent>();

  for (const frame of mockRunScript(runId)) {
    const { event } = frame;
    switch (event.type) {
      case "agent.created":
        agents.set(event.data.id, event.data);
        break;
      case "agent.updated": {
        const { agent_id: agentId, ...changed } = event.data;
        const current = agents.get(agentId);
        if (!current) break;
        agents.set(agentId, {
          ...current,
          ...changed,
          updated_at: event.ts,
        });
        break;
      }
      case "agent.status_changed": {
        const current = agents.get(event.data.agent_id);
        if (!current) break;
        agents.set(event.data.agent_id, {
          ...current,
          status: event.data.status,
          finished_at: event.data.finished_at ?? current.finished_at,
          updated_at: event.ts,
        });
        break;
      }
      case "agent.activity": {
        const current = agents.get(event.data.agent_id);
        if (!current) break;
        agents.set(event.data.agent_id, {
          ...current,
          last_activity: event.data.text,
          updated_at: event.ts,
        });
        break;
      }
      default:
        break;
    }
  }

  return [...agents.values()];
}

/** Clusters the replay would have received from `GET /runs/{id}`. */
export function mockClusters(runId: string = MOCK_RUN_ID): Cluster[] {
  return [
    {
      id: "cl_grip",
      run_id: runId,
      label: "grasp timeout",
      scenario_ids: GRIP_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
      signature: "object_in_bin:failed|grip-short",
      size: GRIP_FAILURES.length,
    },
    {
      id: "cl_latency",
      run_id: runId,
      label: "late approach collision",
      scenario_ids: LATENCY_FAILURES.map((i) => `scn_${String(i).padStart(2, "0")}`),
      signature: "no_collision:failed|latency",
      size: LATENCY_FAILURES.length,
    },
    {
      id: "cl_observation",
      run_id: runId,
      label: "trace observations",
      scenario_ids: [],
      signature: "observation-only|missing-trace",
      size: 0,
    },
  ];
}

export interface ReplayOptions {
  /** Multiply every scripted delay. 1 is demo pace; 0 replays instantly. */
  speed?: number;
  /** Restart from the top once the script ends. */
  loop?: boolean;
}

/**
 * Replay the script through `onEvent`, respecting the scripted timing.
 *
 * @returns a cancel function; call it on unmount.
 */
export function replayMockRun(
  runId: string,
  onEvent: (event: TypedRunEvent) => void,
  options: ReplayOptions = {},
): () => void {
  const { speed = 1, loop = false } = options;
  const script = mockRunScript(runId);
  let index = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;

  const step = (): void => {
    if (cancelled) return;
    if (index >= script.length) {
      if (!loop) return;
      index = 0;
    }
    const frame = script[index];
    index += 1;
    onEvent(frame.event);
    const next = script[index];
    const delay = next ? next.delayMs * speed : 1500 * speed;
    timer = setTimeout(step, Math.max(0, delay));
  };

  timer = setTimeout(step, script[0] ? script[0].delayMs * speed : 0);

  return () => {
    cancelled = true;
    if (timer !== null) clearTimeout(timer);
  };
}
