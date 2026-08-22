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
 *
 * For the live demo id (`run_live_demo`) the scripted replay from
 * `lib/mockLive.ts` is driven through the same pure `applyEvent`, plus the
 * supplementary `applyLiveEvent`, so the wall demos with no backend while
 * exercising the same reducer path the real stream uses.
 */

import { useEffect, useReducer } from "react";

import { LIVE_MOCK_RUN_ID, replayMockLive } from "@/lib/mockLive";
import type { RunState } from "@/lib/useEventStream";
import {
  applyEvent,
  EMPTY_RUN_STATE,
  useEventStream,
} from "@/lib/useEventStream";

import type { LiveState } from "./liveState";
import { applyLiveEvent, EMPTY_LIVE_STATE } from "./liveState";

export function isLiveMockRun(runId: string): boolean {
  return runId === LIVE_MOCK_RUN_ID;
}

interface CombinedState {
  run: RunState;
  live: LiveState;
}

const EMPTY_COMBINED: CombinedState = {
  run: { ...EMPTY_RUN_STATE, connection: "open" },
  live: EMPTY_LIVE_STATE,
};

function combinedReducer(state: CombinedState, event: unknown): CombinedState {
  return {
    run: applyEvent(state.run, event),
    live: applyLiveEvent(state.live, event),
  };
}

export interface LiveRunState extends RunState {
  live: LiveState;
  /** True when this run is the scripted client-side live replay. */
  replay: boolean;
}

export function useLiveRun(runId: string): LiveRunState {
  const mock = isLiveMockRun(runId);

  // Both hooks always run (rules of hooks); the stream stays idle on the mock.
  const streamState = useEventStream(mock ? null : runId);
  const [mockState, dispatch] = useReducer(combinedReducer, EMPTY_COMBINED);

  useEffect(() => {
    if (!mock) return;
    return replayMockLive(runId, (event) => dispatch(event));
  }, [mock, runId]);

  if (mock) {
    return { ...mockState.run, live: mockState.live, replay: true };
  }
  return { ...streamState, live: EMPTY_LIVE_STATE, replay: false };
}
