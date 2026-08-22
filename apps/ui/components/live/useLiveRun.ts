"use client";

/**
 * Run state plus the live-wall supplement.
 *
 * For a real run this wraps `useEventStream` untouched — canonical state stays
 * owned by the one hook. The wall reads live fields (`progress`,
 * `live_frame_path`, `worker_id`) straight off each `Scenario`, which arrive
 * via REST resync today and via the reducer once it forwards
 * `scenario.progress`; the pool is inferred from running scenarios until
 * `worker.pool_changed` is carried in `RunState` (see `effectivePool`).
 */

import type { RunState } from "@/lib/useEventStream";
import { useEventStream } from "@/lib/useEventStream";

import type { LiveState } from "./liveState";
import { EMPTY_LIVE_STATE } from "./liveState";

export interface LiveRunState extends RunState {
  live: LiveState;
  replay: boolean;
}

export function useLiveRun(runId: string): LiveRunState {
  const streamState = useEventStream(runId);
  return { ...streamState, live: EMPTY_LIVE_STATE, replay: false };
}
