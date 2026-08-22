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

import { useCallback, useEffect, useReducer, useRef } from "react";

import * as api from "./api";
import { MOCK_RUN_ID, mockClusters, replayMockRun } from "./mockRun";
import type {
  Agent,
  Cluster,
  Finding,
  Message,
  Report,
  Run,
  RunEvent,
  Scenario,
  TypedRunEvent,
} from "./types";

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

/** Backoff schedule for reconnects, in milliseconds. Capped, not unbounded. */
const BACKOFF_MS = [500, 1000, 2000, 4000, 8000, 15000];

/**
 * True when this run id should be served by the local scripted replay instead
 * of the API. Lets the whole dashboard be built and demoed with no backend.
 */
export function isMockRun(runId: string): boolean {
  return runId === MOCK_RUN_ID || runId.startsWith("mock");
}

type Action =
  | { kind: "event"; event: unknown }
  | { kind: "snapshot"; state: Partial<RunState> }
  | { kind: "connection"; connection: ConnectionState }
  | { kind: "error"; error: string | null };

function upsertById<T extends { id: string }>(items: T[], item: T): T[] {
  const at = items.findIndex((existing) => existing.id === item.id);
  if (at === -1) return [...items, item];
  const next = items.slice();
  next[at] = { ...next[at], ...item };
  return next;
}

function patchById<T extends { id: string }>(
  items: T[],
  id: string,
  patch: Partial<T>,
): T[] {
  const at = items.findIndex((existing) => existing.id === id);
  if (at === -1) return items;
  const next = items.slice();
  next[at] = { ...next[at], ...patch };
  return next;
}

/** Structural check — the stream is untrusted input, however friendly. */
function isRunEvent(value: unknown): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RunEvent>;
  return (
    typeof candidate.id === "string" &&
    typeof candidate.type === "string" &&
    typeof candidate.data === "object" &&
    candidate.data !== null
  );
}

/**
 * Apply one event to the state. Pure, exported so it can be unit-tested and
 * replayed over a recorded event log — which is also how the demo runs without
 * a live backend.
 */
export function applyEvent(state: RunState, event: unknown): RunState {
  if (!isRunEvent(event)) return state;

  const seq = typeof event.seq === "number" ? event.seq : state.seq + 1;
  // Replay after reconnect redelivers what we already applied.
  if (seq <= state.seq) return state;

  const typed = event as TypedRunEvent;
  const next: RunState = { ...state, seq };

  switch (typed.type) {
    case "run.created":
    case "run.finished":
      next.run = typed.data;
      break;

    case "run.stage_changed":
      // A partial patch, not a full object: the client already holds the run.
      next.run = next.run
        ? { ...next.run, stage: typed.data.stage }
        : next.run;
      break;

    case "agent.created":
      next.agents = upsertById(state.agents, typed.data);
      break;

    case "agent.status_changed":
      next.agents = patchById(state.agents, typed.data.agent_id, {
        status: typed.data.status,
        updated_at: typed.ts,
      });
      break;

    case "agent.activity":
      next.agents = patchById(state.agents, typed.data.agent_id, {
        last_activity: typed.data.text,
        updated_at: typed.data.ts ?? typed.ts,
      });
      break;

    case "message.sent":
      next.messages = upsertById(state.messages, typed.data);
      break;

    case "scenario.created":
    case "scenario.finished":
      next.scenarios = upsertById(state.scenarios, typed.data);
      break;

    case "scenario.started":
      next.scenarios = patchById(state.scenarios, typed.data.scenario_id, {
        status: "running",
      });
      break;

    case "suite.progress":
      next.run = next.run
        ? {
            ...next.run,
            suite: {
              total: typed.data.total,
              passed: typed.data.passed,
              failed: typed.data.failed,
              pass_rate:
                typed.data.completed > 0
                  ? typed.data.passed / typed.data.completed
                  : 0,
              baseline_pass_rate: next.run.suite?.baseline_pass_rate ?? null,
            },
          }
        : next.run;
      break;

    case "finding.created":
      next.findings = upsertById(state.findings, typed.data);
      break;

    case "finding.updated":
      next.findings = patchById(state.findings, typed.data.finding_id, {
        status: typed.data.status,
        superseded_by: typed.data.superseded_by ?? null,
      });
      break;

    case "artifact.created": {
      const scenarioId = typed.data.scenario_id;
      if (typed.data.kind === "video" && scenarioId) {
        next.scenarios = patchById(state.scenarios, scenarioId, {
          video_path: typed.data.path,
        });
      }
      break;
    }

    case "report.created":
      next.report = typed.data;
      next.run = next.run
        ? {
            ...next.run,
            report_id: typed.data.id,
            pull_request_url:
              typed.data.pull_request_url ?? next.run.pull_request_url,
          }
        : next.run;
      break;

    case "error":
      // Never a failing scenario — this is our system breaking.
      next.error = typed.data.message;
      break;
  }

  return next;
}

function reducer(state: RunState, action: Action): RunState {
  switch (action.kind) {
    case "event":
      return applyEvent(state, action.event);
    case "snapshot":
      return { ...state, ...action.state };
    case "connection":
      return { ...state, connection: action.connection };
    case "error":
      return { ...state, error: action.error };
  }
}

/**
 * Subscribe to a run's event stream.
 *
 * @param runId - run to follow, or null to stay idle (the index page).
 */
export function useEventStream(runId: string | null): RunState {
  const [state, dispatch] = useReducer(reducer, EMPTY_RUN_STATE);
  const seqRef = useRef(0);

  seqRef.current = state.seq;

  /** First paint from REST, so the page is never blank while the socket opens. */
  const resync = useCallback(
    async (id: string): Promise<void> => {
      const detail = await api.getRun(id);
      const [agents, messages, findings] = await Promise.all([
        api.getAgents(id).catch(() => [] as Agent[]),
        api.getMessages(id).catch(() => [] as Message[]),
        api.getFindings(id).catch(() => [] as Finding[]),
      ]);
      const report = detail.report_id
        ? await api.getReport(id).catch(() => null)
        : null;
      const { scenarios, clusters, ...run } = detail;
      dispatch({
        kind: "snapshot",
        state: {
          run: run as Run,
          scenarios,
          clusters,
          agents,
          messages,
          findings,
          report,
          error: null,
        },
      });
    },
    [dispatch],
  );

  useEffect(() => {
    if (runId === null) return;

    // --- scripted replay: no backend involved ------------------------------
    if (isMockRun(runId)) {
      dispatch({ kind: "snapshot", state: { connection: "open" } });
      return replayMockRun(runId, (event) => {
        dispatch({ kind: "event", event });
        // Clusters arrive from `GET /runs/{id}` in a real run; in the replay
        // they land when the pipeline reaches the stage that produces them, so
        // the grid visibly regroups instead of starting grouped.
        if (
          event.type === "run.stage_changed" &&
          event.data.stage === "CLUSTER_FAILURES"
        ) {
          dispatch({ kind: "snapshot", state: { clusters: mockClusters(runId) } });
        }
      });
    }

    let closed = false;
    let socket: WebSocket | null = null;
    let retry = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    void resync(runId).catch((err: Error) => {
      dispatch({ kind: "error", error: err.message });
    });

    const connect = (): void => {
      if (closed) return;
      dispatch({
        kind: "connection",
        connection: retry === 0 ? "connecting" : "reconnecting",
      });

      const ws = new WebSocket(`${WS_BASE}/ws/runs/${runId}?since=${seqRef.current}`);
      socket = ws;

      ws.onopen = () => {
        retry = 0;
        dispatch({ kind: "connection", connection: "open" });
      };

      ws.onmessage = (frame: MessageEvent<string>) => {
        let payload: unknown;
        try {
          payload = JSON.parse(frame.data);
        } catch {
          return;
        }
        if (!isRunEvent(payload)) return;

        const incoming = payload.seq;
        const applied = seqRef.current;
        // A gap means we fell behind: refetch rather than render a state we
        // cannot reconstruct. Silently continuing is subtly wrong, which is
        // worse than visibly reloading.
        if (typeof incoming === "number" && applied > 0 && incoming > applied + 1) {
          void resync(runId).catch((err: Error) => {
            dispatch({ kind: "error", error: err.message });
          });
          return;
        }
        dispatch({ kind: "event", event: payload });
      };

      ws.onerror = () => {
        // `onclose` always follows; the backoff lives there.
      };

      ws.onclose = (closeEvent: CloseEvent) => {
        if (closed) return;
        // 1000 after run.finished is the server saying "we are done".
        if (closeEvent.code === 1000) {
          dispatch({ kind: "connection", connection: "closed" });
          return;
        }
        const wait = BACKOFF_MS[Math.min(retry, BACKOFF_MS.length - 1)];
        retry += 1;
        dispatch({ kind: "connection", connection: "reconnecting" });
        reconnectTimer = setTimeout(connect, wait);
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      if (socket !== null) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [runId, resync]);

  return state;
}
