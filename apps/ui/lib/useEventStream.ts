"use client";

/**
 * Live run state over a WebSocket.
 *
 * Responsibility: hold the entire mission-control page's state and keep it
 * current from `WS /ws/runs/{id}`. Components read from this hook and render;
 * none of them fetch or subscribe on their own.
 *
 * Why one hook for everything: the events for a run are a single ordered
 * stream, and splitting them across per-component subscriptions would mean N
 * sockets, N reconnect policies, and components disagreeing about `seq`.
 *
 * Reconnection contract (see docs/EVENT_PROTOCOL.md):
 *  - track the highest `seq` seen;
 *  - reconnect with `?since=<seq>` and lose nothing;
 *  - a gap in `seq` means we fell behind — refetch `GET /runs/{id}` rather than
 *    trying to reconstruct state from a partial stream.
 */

import type { Agent, Cluster, Finding, Message, Report, Run, Scenario } from "./types";

export const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE ?? "ws://localhost:8000";

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

/** Everything the mission control page renders from. */
export interface RunState {
  run: Run | null;
  agents: Agent[];
  messages: Message[];
  scenarios: Scenario[];
  clusters: Cluster[];
  findings: Finding[];
  report: Report | null;
  connection: ConnectionState;
  /** Highest event seq applied. Used for reconnect and gap detection. */
  seq: number;
  error: string | null;
}

export const EMPTY_RUN_STATE: RunState = {
  run: null,
  agents: [],
  messages: [],
  scenarios: [],
  clusters: [],
  findings: [],
  report: null,
  connection: "connecting",
  seq: 0,
  error: null,
};

/**
 * Subscribe to a run's event stream.
 *
 * @param runId - run to follow, or null to stay idle (the index page).
 */
export function useEventStream(runId: string | null): RunState {
  // TODO(build): useReducer over RunEvent, useEffect opening the socket,
  // exponential backoff on close, cleanup on unmount. Seed initial state from
  // api.getRun() so first paint is not empty.
  return EMPTY_RUN_STATE;
}

/**
 * Apply one event to the state. Pure, exported so it can be unit-tested and
 * replayed over a recorded event log — which is also how the demo runs without
 * a live backend.
 */
export function applyEvent(state: RunState, event: unknown): RunState {
  // TODO(build): switch on event.type; upsert by id for agent/scenario/finding,
  // append for message, replace for run/report. Ignore events whose seq is
  // already applied so replay is idempotent.
  return state;
}
