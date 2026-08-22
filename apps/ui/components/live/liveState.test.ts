/**
 * Unit tests for the live wall's pure state: the supplementary reducer, the
 * derived counters (which must never drift under replay), and the merge of
 * live ticks onto canonical scenarios.
 */

import { describe, expect, it } from "vitest";

import type { Agent, Scenario, TypedRunEvent } from "@/lib/types";

import {
  applyLiveEvent,
  countScenarios,
  countWorkingAgents,
  effectivePool,
  EMPTY_LIVE_STATE,
  liveFrameUrl,
  liveStreamUrl,
  mergeLive,
} from "./liveState";

function event<T extends TypedRunEvent["type"]>(
  seq: number,
  type: T,
  data: Extract<TypedRunEvent, { type: T }>["data"],
): TypedRunEvent {
  return {
    id: `evt_${seq}`,
    run_id: "run_x",
    seq,
    type,
    ts: "2026-08-22T12:00:00.000Z",
    data,
  } as TypedRunEvent;
}

function scenario(overrides: Partial<Scenario> = {}): Scenario {
  return {
    id: "scn_00",
    run_id: "run_x",
    index: 0,
    seed: 1234,
    label: "light cube, high friction",
    params: {},
    status: "running",
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
    ...overrides,
  };
}

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agt_0",
    run_id: "run_x",
    session_id: null,
    session_url: null,
    role: "fixer",
    title: "Fix Engineer",
    task: "fix it",
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
    created_at: "2026-08-22T12:00:00.000Z",
    updated_at: "2026-08-22T12:00:00.000Z",
    finished_at: null,
    ...overrides,
  };
}

describe("applyLiveEvent", () => {
  it("stores the worker pool from worker.pool_changed", () => {
    const state = applyLiveEvent(
      EMPTY_LIVE_STATE,
      event(1, "worker.pool_changed", {
        workers: 6,
        busy: 4,
        queued: 9,
        reason: "verify fan-out: 3 clusters",
      }),
    );
    expect(state.pool).toEqual({
      workers: 6,
      busy: 4,
      queued: 9,
      reason: "verify fan-out: 3 clusters",
      ts: "2026-08-22T12:00:00.000Z",
    });
  });

  it("tracks per-scenario progress and worker across started/progress", () => {
    let state = applyLiveEvent(
      EMPTY_LIVE_STATE,
      event(1, "scenario.started", { scenario_id: "scn_00", worker_id: "wkr_2" }),
    );
    state = applyLiveEvent(
      state,
      event(2, "scenario.progress", {
        scenario_id: "scn_00",
        progress: 0.5,
        sim_time_s: 4.0,
        live_frame_path: "run_x/scn_00_live.jpg",
      }),
    );
    expect(state.progress["scn_00"]).toEqual({
      progress: 0.5,
      sim_time_s: 4.0,
      live_frame_path: "run_x/scn_00_live.jpg",
      worker_id: "wkr_2",
    });
  });

  it("clears progress when the scenario finishes", () => {
    let state = applyLiveEvent(
      EMPTY_LIVE_STATE,
      event(1, "scenario.progress", {
        scenario_id: "scn_00",
        progress: 0.9,
        sim_time_s: 7.2,
      }),
    );
    state = applyLiveEvent(
      state,
      event(2, "scenario.finished", scenario({ status: "passed" })),
    );
    expect(state.progress["scn_00"]).toBeUndefined();
  });

  it("is idempotent under replayed events", () => {
    const tick = event(5, "scenario.progress", {
      scenario_id: "scn_00",
      progress: 0.25,
      sim_time_s: 2.0,
    });
    const once = applyLiveEvent(EMPTY_LIVE_STATE, tick);
    const stale = event(3, "scenario.progress", {
      scenario_id: "scn_00",
      progress: 0.1,
      sim_time_s: 0.8,
    });
    const replayed = applyLiveEvent(once, stale);
    expect(replayed).toBe(once);
  });

  it("ignores malformed input", () => {
    expect(applyLiveEvent(EMPTY_LIVE_STATE, null)).toBe(EMPTY_LIVE_STATE);
    expect(applyLiveEvent(EMPTY_LIVE_STATE, { type: 42 })).toBe(EMPTY_LIVE_STATE);
  });
});

describe("mergeLive", () => {
  it("overlays the latest tick onto a running scenario", () => {
    const state = applyLiveEvent(
      EMPTY_LIVE_STATE,
      event(1, "scenario.progress", {
        scenario_id: "scn_00",
        progress: 0.75,
        sim_time_s: 6.0,
        live_frame_path: "run_x/scn_00_live.jpg",
      }),
    );
    const merged = mergeLive(scenario(), state);
    expect(merged.progress).toBe(0.75);
    expect(merged.sim_time_s).toBe(6.0);
    expect(merged.live_frame_path).toBe("run_x/scn_00_live.jpg");
  });

  it("leaves finished scenarios untouched", () => {
    const state = applyLiveEvent(
      EMPTY_LIVE_STATE,
      event(1, "scenario.progress", {
        scenario_id: "scn_00",
        progress: 0.75,
        sim_time_s: 6.0,
      }),
    );
    const done = scenario({ status: "passed", progress: 1 });
    expect(mergeLive(done, state)).toBe(done);
  });
});

describe("derived counters", () => {
  it("counts scenarios by status from the list itself", () => {
    const counts = countScenarios([
      scenario({ id: "a", status: "running" }),
      scenario({ id: "b", status: "running" }),
      scenario({ id: "c", status: "pending" }),
      scenario({ id: "d", status: "passed" }),
      scenario({ id: "e", status: "failed" }),
      scenario({ id: "f", status: "error" }),
    ]);
    expect(counts).toEqual({
      total: 6,
      pending: 1,
      running: 2,
      passed: 1,
      failed: 1,
      error: 1,
    });
  });

  it("counts working agents (working/starting/blocked, not finished)", () => {
    const count = countWorkingAgents([
      agent({ id: "a", status: "working" }),
      agent({ id: "b", status: "starting" }),
      agent({ id: "c", status: "blocked" }),
      agent({ id: "d", status: "succeeded" }),
      agent({ id: "e", status: "failed" }),
      agent({ id: "f", status: "queued" }),
    ]);
    expect(count).toBe(3);
  });

  it("prefers the explicit pool, infers from scenarios otherwise", () => {
    const explicit = {
      workers: 6,
      busy: 2,
      queued: 1,
      reason: null,
      ts: "",
    };
    expect(effectivePool(explicit, [])).toBe(explicit);

    const inferred = effectivePool(null, [
      scenario({ id: "a", status: "running", worker_id: "wkr_0" }),
      scenario({ id: "b", status: "running", worker_id: "wkr_1" }),
      scenario({ id: "c", status: "pending" }),
    ]);
    expect(inferred).toMatchObject({ workers: 2, busy: 2, queued: 1 });

    expect(effectivePool(null, [scenario({ status: "passed" })])).toBeNull();
  });
});

describe("feed URLs", () => {
  it("builds the documented live endpoints", () => {
    expect(liveStreamUrl("run_1", "scn_2")).toMatch(
      /\/runs\/run_1\/scenarios\/scn_2\/live\.mjpg$/,
    );
    expect(liveFrameUrl("run_1", "scn_2")).toMatch(
      /\/runs\/run_1\/scenarios\/scn_2\/live\.jpg$/,
    );
  });
});
