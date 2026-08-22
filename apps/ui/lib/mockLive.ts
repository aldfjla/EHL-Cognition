/**
 * A scripted *live* run, replayed client-side.
 *
 * `mockRun.ts` proves the whole pipeline renders; this module exists for the
 * live simulation wall: many scenarios running at once, workers picking them
 * up, progress ticking, some feeds unavailable, the pool resizing mid-run.
 * It emits exactly the event shapes in docs/EVENT_PROTOCOL.md so the wall can
 * be developed and demoed with no API at all.
 *
 * Like the pipeline replay, it is labelled: the commit message carries the
 * `[REPLAY]` prefix. A replay must never be presentable as a live run.
 */

import type {
  Agent,
  EventPayloads,
  EventType,
  Run,
  Scenario,
  TypedRunEvent,
} from "./types";

export const LIVE_MOCK_RUN_ID = "run_live_demo";

/** One scripted frame: how long to wait, then what arrives. */
export interface ScriptedLiveEvent {
  /** Delay after the previous frame, in milliseconds. */
  delayMs: number;
  event: TypedRunEvent;
}

const REPO = "tumai/panda-pick-and-place";
const SHA = "5e2d91cc7ab04f6e8d3b12a9f0c47d8e6b1a3f52";
const SCENARIO_COUNT = 18;
const WORKER_COUNT = 6;
/** Scenarios whose worker never publishes a frame — the wall must stay honest. */
const FEEDLESS = [4, 11];
/** Seeds that fail, so finished tiles show both outcomes. */
const FAILURES = [2, 7, 13];
/** How many progress ticks a scenario takes from start to finish. */
const TICKS = 8;

let clockMs = Date.now();

function tick(ms: number): string {
  clockMs += ms;
  return new Date(clockMs).toISOString();
}

function scenarioLabel(index: number): string {
  const payloads = ["light cube", "medium cube", "heavy cube"];
  const surfaces = ["high friction", "nominal friction", "low friction"];
  return `${payloads[index % 3]}, ${surfaces[Math.floor(index / 3) % 3]}`;
}

function makeScenario(runId: string, index: number): Scenario {
  return {
    id: `scn_${String(index).padStart(2, "0")}`,
    run_id: runId,
    index,
    seed: 7100 + index * 13,
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
    cluster_id: null,
    video_path: null,
    live_frame_path: null,
    worker_id: null,
    progress: null,
    trace_path: null,
    error: null,
  };
}

function finishScenario(scenario: Scenario, workerId: string): Scenario {
  const failed = FAILURES.includes(scenario.index);
  return {
    ...scenario,
    status: failed ? "failed" : "passed",
    duration_s: Number((4.1 + (scenario.index % 5) * 0.27).toFixed(2)),
    sim_time_s: failed ? 8.0 : 6.2,
    worker_id: workerId,
    progress: 1,
    live_frame_path: null,
    criteria: [
      { id: "object_in_bin", passed: !failed, value: failed ? 0 : 1, threshold: 1 },
      {
        id: "no_collision",
        passed: true,
        value: 0.0,
        threshold: 0.005,
      },
    ],
    diagnosis: failed
      ? "Gripper closed 38mm before reaching the cube; object never left the table."
      : null,
    video_path: failed ? `${scenario.run_id}/${scenario.id}_before.mp4` : null,
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
    status: "working",
    iteration: 1,
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
 * Build the scripted event log.
 *
 * Deterministic in structure and ordering; timestamps are anchored to the wall
 * clock at build time so elapsed times look plausible.
 */
export function mockLiveScript(runId: string = LIVE_MOCK_RUN_ID): ScriptedLiveEvent[] {
  clockMs = Date.now();
  const script: ScriptedLiveEvent[] = [];
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
        id: `evt_live_${String(seq).padStart(4, "0")}`,
        run_id: runId,
        seq,
        type,
        ts: tick(100),
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
    commit_message: "[REPLAY] widen approach cone for off-centre grasps",
    pushed_by: "arm-team",
    robot_model: {
      source: "menagerie",
      name: "franka_emika_panda",
      model_path: "vendor/menagerie/franka_emika_panda/panda.xml",
      dof: 7,
      confidence: 0.97,
    },
    suite: null,
    pull_request_url: null,
    report_id: null,
    error: null,
    created_at: new Date(clockMs).toISOString(),
    updated_at: new Date(clockMs).toISOString(),
    finished_at: null,
  };

  push(0, "run.created", run);
  push(400, "run.stage_changed", { stage: "RESOLVE_MODEL", previous_stage: "TRIGGERED" });
  push(400, "run.stage_changed", {
    stage: "BUILD_HARNESS",
    previous_stage: "RESOLVE_MODEL",
  });

  const harness = makeAgent(
    runId,
    "agt_harness",
    "harness_builder",
    "Test Infra — MuJoCo harness",
    "Bind src/controller.py:run to MuJoCo actuators.",
    { status: "succeeded", step: "handoff" },
  );
  push(300, "agent.created", harness);

  const designer = makeAgent(
    runId,
    "agt_qa",
    "scenario_designer",
    "QA Lead — randomization ranges",
    "Randomize payload, friction, noise and latency across the approach envelope.",
    { status: "working", step: "matrix designed" },
  );
  push(300, "run.stage_changed", {
    stage: "DESIGN_SCENARIOS",
    previous_stage: "BUILD_HARNESS",
  });
  push(200, "agent.created", designer);

  // --- RUN_SUITE: the part the wall exists for -----------------------------
  const scenarios = Array.from({ length: SCENARIO_COUNT }, (_, i) =>
    makeScenario(runId, i),
  );
  push(500, "run.stage_changed", {
    stage: "RUN_SUITE",
    previous_stage: "DESIGN_SCENARIOS",
  });
  push(100, "agent.status_changed", {
    agent_id: designer.id,
    status: "succeeded",
    previous_status: "working",
  });
  for (const scenario of scenarios) {
    push(30, "scenario.created", scenario);
  }
  push(200, "worker.pool_changed", {
    workers: WORKER_COUNT,
    busy: 0,
    queued: SCENARIO_COUNT,
    reason: `suite fan-out: ${SCENARIO_COUNT} scenarios`,
  });

  // Interleaved simulation: each worker runs one scenario at a time; a new one
  // is picked up as soon as a slot frees. Progress ticks are throttled the way
  // the real worker throttles them.
  const queue = scenarios.map((s) => s.index);
  interface Slot {
    worker: string;
    index: number;
    ticksDone: number;
  }
  const slots: Slot[] = [];
  let completed = 0;
  let passed = 0;
  let failed = 0;

  const start = (): void => {
    const index = queue.shift();
    if (index === undefined) return;
    const worker = `wkr_${slots.length % WORKER_COUNT}`;
    const usedWorkers = new Set(slots.map((s) => s.worker));
    let workerId = worker;
    for (let w = 0; w < WORKER_COUNT; w += 1) {
      const candidate = `wkr_${w}`;
      if (!usedWorkers.has(candidate)) {
        workerId = candidate;
        break;
      }
    }
    slots.push({ worker: workerId, index, ticksDone: 0 });
    push(80, "scenario.started", {
      scenario_id: scenarios[index].id,
      worker_id: workerId,
    });
    push(60, "worker.pool_changed", {
      workers: WORKER_COUNT,
      busy: slots.length,
      queued: queue.length,
      reason: undefined,
    });
  };

  for (let i = 0; i < WORKER_COUNT; i += 1) start();

  // Round-robin ticks until everything has finished.
  while (slots.length > 0) {
    for (let s = 0; s < slots.length; s += 1) {
      const slot = slots[s];
      slot.ticksDone += 1;
      const scenario = scenarios[slot.index];
      const progress = Math.min(1, slot.ticksDone / TICKS);
      if (slot.ticksDone < TICKS) {
        push(320, "scenario.progress", {
          scenario_id: scenario.id,
          progress: Number(progress.toFixed(3)),
          sim_time_s: Number((progress * 8).toFixed(2)),
          live_frame_path: FEEDLESS.includes(slot.index)
            ? null
            : `${runId}/${scenario.id}_live.jpg`,
        });
      } else {
        const finished = finishScenario(scenario, slot.worker);
        push(320, "scenario.finished", finished);
        completed += 1;
        if (finished.status === "passed") passed += 1;
        else failed += 1;
        if (finished.video_path) {
          push(20, "artifact.created", {
            kind: "video",
            path: finished.video_path,
            scenario_id: finished.id,
            run_id: runId,
          });
        }
        slots.splice(s, 1);
        s -= 1;
        if (completed % 3 === 0 || completed === SCENARIO_COUNT) {
          push(40, "suite.progress", {
            total: SCENARIO_COUNT,
            completed,
            passed,
            failed,
            running: slots.length,
            workers: WORKER_COUNT,
          });
        }
        start();
      }
    }
  }

  push(300, "worker.pool_changed", {
    workers: WORKER_COUNT,
    busy: 0,
    queued: 0,
    reason: "suite drained",
  });
  push(500, "run.stage_changed", {
    stage: "CLUSTER_FAILURES",
    previous_stage: "RUN_SUITE",
  });
  push(800, "run.finished", {
    ...run,
    stage: "FAILED_UNRESOLVED",
    suite: {
      total: SCENARIO_COUNT,
      passed,
      failed,
      pass_rate: SCENARIO_COUNT > 0 ? passed / SCENARIO_COUNT : 0,
      baseline_pass_rate: null,
    },
    updated_at: new Date(clockMs).toISOString(),
    finished_at: new Date(clockMs).toISOString(),
  } satisfies Run);

  return script;
}

export interface LiveReplayOptions {
  /** Multiply every scripted delay. 1 is demo pace; 0 replays instantly. */
  speed?: number;
}

/**
 * Replay the script through `onEvent`, respecting the scripted timing.
 *
 * @returns a cancel function; call it on unmount.
 */
export function replayMockLive(
  runId: string,
  onEvent: (event: TypedRunEvent) => void,
  options: LiveReplayOptions = {},
): () => void {
  const { speed = 1 } = options;
  const script = mockLiveScript(runId);
  let index = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;

  const step = (): void => {
    if (cancelled || index >= script.length) return;
    const frame = script[index];
    index += 1;
    onEvent(frame.event);
    const next = script[index];
    if (!next) return;
    timer = setTimeout(step, Math.max(0, next.delayMs * speed));
  };

  timer = setTimeout(step, script[0] ? script[0].delayMs * speed : 0);

  return () => {
    cancelled = true;
    if (timer !== null) clearTimeout(timer);
  };
}
