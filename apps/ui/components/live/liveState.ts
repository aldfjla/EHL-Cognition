/**
 * Pure state for the live simulation wall.
 *
 * `useEventStream` owns the run's canonical state; this module owns the
 * *supplementary* live-only state the reducer does not yet carry — per-scenario
 * progress ticks and the worker pool — plus the derived counts the header
 * shows. Everything here is pure so it can be unit-tested and replayed.
 *
 * Counts are derived from the scenario list itself (upserted by id), not by
 * incrementing on events, so a replayed or re-delivered event can never make a
 * counter drift.
 */

import { API_BASE } from "@/lib/api";
import type {
  Agent,
  RunEvent,
  Scenario,
  ScenarioStatus,
  TypedRunEvent,
} from "@/lib/types";

// ---- worker pool ------------------------------------------------------------

export interface WorkerPool {
  workers: number;
  busy: number;
  queued: number;
  reason: string | null;
  ts: string;
}

/** Per-scenario live progress, keyed by scenario id. */
export interface LiveProgress {
  progress: number;
  sim_time_s: number;
  live_frame_path: string | null;
  worker_id: string | null;
}

export interface LiveState {
  pool: WorkerPool | null;
  /** Latest progress tick per scenario. Cleared when the scenario finishes. */
  progress: Record<string, LiveProgress>;
  /** Highest seq applied, for idempotent replay. */
  seq: number;
}

export const EMPTY_LIVE_STATE: LiveState = {
  pool: null,
  progress: {},
  seq: 0,
};

function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RunEvent>;
  return (
    typeof candidate.type === "string" &&
    typeof candidate.data === "object" &&
    candidate.data !== null
  );
}

/**
 * Apply one event to the supplementary live state. Pure and idempotent:
 * replayed events (seq <= applied) are ignored, like `applyEvent`.
 */
export function applyLiveEvent(state: LiveState, event: unknown): LiveState {
  if (!isRunEvent(event)) return state;
  const seq = typeof event.seq === "number" ? event.seq : state.seq + 1;
  if (seq <= state.seq) return state;

  const typed = event as TypedRunEvent;

  switch (typed.type) {
    case "worker.pool_changed":
      return {
        ...state,
        seq,
        pool: {
          workers: typed.data.workers,
          busy: typed.data.busy,
          queued: typed.data.queued,
          reason: typed.data.reason ?? null,
          ts: typed.ts,
        },
      };

    case "scenario.started": {
      const existing = state.progress[typed.data.scenario_id];
      return {
        ...state,
        seq,
        progress: {
          ...state.progress,
          [typed.data.scenario_id]: {
            progress: existing?.progress ?? 0,
            sim_time_s: existing?.sim_time_s ?? 0,
            live_frame_path: existing?.live_frame_path ?? null,
            worker_id: typed.data.worker_id ?? null,
          },
        },
      };
    }

    case "scenario.progress": {
      const existing = state.progress[typed.data.scenario_id];
      return {
        ...state,
        seq,
        progress: {
          ...state.progress,
          [typed.data.scenario_id]: {
            progress: typed.data.progress,
            sim_time_s: typed.data.sim_time_s,
            live_frame_path: typed.data.live_frame_path ?? null,
            worker_id: existing?.worker_id ?? null,
          },
        },
      };
    }

    case "scenario.finished": {
      if (!(typed.data.id in state.progress)) return { ...state, seq };
      const progress = { ...state.progress };
      delete progress[typed.data.id];
      return { ...state, seq, progress };
    }

    default:
      return { ...state, seq };
  }
}

/**
 * A scenario as the wall renders it: the canonical scenario with the latest
 * live tick merged over its (possibly stale) live fields.
 */
export function mergeLive(scenario: Scenario, live: LiveState): Scenario {
  const tickData = live.progress[scenario.id];
  if (!tickData || scenario.status !== "running") return scenario;
  return {
    ...scenario,
    progress: tickData.progress,
    sim_time_s: tickData.sim_time_s,
    live_frame_path: tickData.live_frame_path ?? scenario.live_frame_path,
    worker_id: tickData.worker_id ?? scenario.worker_id,
  };
}

// ---- derived counts ----------------------------------------------------------

export interface ScenarioCounts {
  total: number;
  pending: number;
  running: number;
  passed: number;
  failed: number;
  error: number;
}

/** Count by status from the scenario list itself — drift-free by construction. */
export function countScenarios(scenarios: Scenario[]): ScenarioCounts {
  const counts: ScenarioCounts = {
    total: scenarios.length,
    pending: 0,
    running: 0,
    passed: 0,
    failed: 0,
    error: 0,
  };
  for (const scenario of scenarios) {
    counts[scenario.status satisfies ScenarioStatus] += 1;
  }
  return counts;
}

/** Agents actively doing something, as opposed to done or waiting. */
export function countWorkingAgents(agents: Agent[]): number {
  return agents.filter(
    (agent) =>
      agent.status === "working" ||
      agent.status === "starting" ||
      agent.status === "blocked",
  ).length;
}

/**
 * The pool as shown: the explicit `worker.pool_changed` state when we have
 * one, otherwise inferred from the scenarios (distinct workers busy).
 */
export function effectivePool(
  pool: WorkerPool | null,
  scenarios: Scenario[],
): WorkerPool | null {
  if (pool) return pool;
  const busyWorkers = new Set(
    scenarios
      .filter((s) => s.status === "running" && s.worker_id)
      .map((s) => s.worker_id as string),
  );
  const pending = scenarios.filter((s) => s.status === "pending").length;
  if (busyWorkers.size === 0 && pending === 0) return null;
  return {
    workers: busyWorkers.size,
    busy: busyWorkers.size,
    queued: pending,
    reason: null,
    ts: "",
  };
}

// ---- feed URLs ----------------------------------------------------------------

/** The MJPEG stream for a running scenario. Only open one per *visible* tile. */
export function liveStreamUrl(runId: string, scenarioId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/scenarios/${encodeURIComponent(scenarioId)}/live.mjpg`;
}

/** A single frame — thumbnails, off-screen tiles, and the fallback path. */
export function liveFrameUrl(runId: string, scenarioId: string): string {
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/scenarios/${encodeURIComponent(scenarioId)}/live.jpg`;
}
