import { describe, expect, it } from "vitest";

import {
  EMPTY_RUN_STATE,
  acceptEventSeq,
  applyEvent,
  beginResync,
  completeResync,
  recordResyncStart,
  reduceRunState,
  resetResyncTracker,
  type EventCursor,
  type ResyncTracker,
} from "./useEventStream";
import type { Agent } from "./types";

function event(seq: number, type: string, data: Record<string, unknown>) {
  return {
    id: `evt-${seq}`,
    run_id: "run-test",
    seq,
    type,
    ts: new Date(0).toISOString(),
    data,
  };
}

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agent-a",
    run_id: "run-test",
    session_id: "session-a",
    session_url: "https://app.devin.ai/sessions/a",
    role: "fixer",
    title: "Fixer",
    task: "Fix the failure",
    status: "working",
    iteration: 1,
    max_iterations: 3,
    cluster_id: "cluster-a",
    scenario_ids: ["scenario-a"],
    parent_agent_id: null,
    finding_ids: [],
    last_activity: "checking the patch",
    desktop_url: "/mock/desktop/index.html",
    issue: "grasp timeout",
    step: "running verification",
    created_at: "2025-01-01T00:00:00.000Z",
    updated_at: "2025-01-01T00:01:00.000Z",
    finished_at: null,
    ...overrides,
  };
}

describe("event stream sequencing", () => {
  it("marks a run as missing without changing its rendered data", () => {
    const state = {
      ...EMPTY_RUN_STATE,
      error: "previous error",
    };

    expect(
      reduceRunState(state, { kind: "missing", missing: true }),
    ).toEqual({
      ...state,
      error: null,
      missing: true,
    });
  });

  it("applies a burst of contiguous events arriving in one tick", () => {
    const cursor: EventCursor = { appliedSeq: 0, resyncInFlight: false };
    let state = EMPTY_RUN_STATE;

    for (let seq = 1; seq <= 3; seq += 1) {
      expect(acceptEventSeq(cursor, seq)).toBe("apply");
      state = applyEvent(
        state,
        event(seq, "run.stage_changed", {
          stage: seq === 3 ? "RUN_SUITE" : "TRIGGERED",
          previous_stage: null,
        }),
      );
    }

    expect(cursor.appliedSeq).toBe(3);
    expect(state.seq).toBe(3);
  });

  it("starts only one resync while a genuine gap is in flight", () => {
    const cursor: EventCursor = { appliedSeq: 2, resyncInFlight: false };

    expect(acceptEventSeq(cursor, 4)).toBe("gap");
    expect(beginResync(cursor)).toBe(true);
    expect(acceptEventSeq(cursor, 5)).toBe("gap");
    expect(beginResync(cursor)).toBe(false);
    expect(cursor.appliedSeq).toBe(2);
  });

  it("advances the cursor to the highest sequence in the resync replay", () => {
    const cursor: EventCursor = { appliedSeq: 2, resyncInFlight: false };

    expect(beginResync(cursor)).toBe(true);
    expect(completeResync(cursor, [{ seq: 3 }, { seq: 7 }, { seq: 5 }])).toBe(7);
    expect(cursor).toEqual({ appliedSeq: 7, resyncInFlight: true });
  });

  it("ignores replayed duplicates without changing state", () => {
    const first = event(1, "message.sent", {
      id: "message-1",
      run_id: "run-test",
      sender_id: "agent-a",
      recipient_id: "agent-b",
      kind: "status",
      body: "ready",
      refs: [],
      created_at: new Date(0).toISOString(),
    });
    const once = applyEvent(EMPTY_RUN_STATE, first);
    const twice = applyEvent(once, first);

    expect(twice).toBe(once);
  });

  it("does not regress state for out-of-order sequences", () => {
    const cursor: EventCursor = { appliedSeq: 3, resyncInFlight: false };
    const state = { ...EMPTY_RUN_STATE, seq: 3 };
    const older = event(2, "run.stage_changed", {
      stage: "TRIGGERED",
      previous_stage: null,
    });

    expect(acceptEventSeq(cursor, 2)).toBe("duplicate");
    expect(applyEvent(state, older)).toBe(state);
    expect(cursor.appliedSeq).toBe(3);
  });

  it("latches a resync storm until a fresh connection resets it", () => {
    const tracker: ResyncTracker = { history: [], stormed: false };

    expect(recordResyncStart(tracker, 0)).toBe(true);
    expect(recordResyncStart(tracker, 1)).toBe(true);
    expect(recordResyncStart(tracker, 2)).toBe(true);
    expect(recordResyncStart(tracker, 3)).toBe(false);
    expect(tracker.stormed).toBe(true);
    expect(recordResyncStart(tracker, 4)).toBe(false);

    resetResyncTracker(tracker);

    expect(tracker).toEqual({ history: [], stormed: false });
    expect(recordResyncStart(tracker, 4)).toBe(true);
  });

  it("applies agent.updated fields without clobbering or leaking agent_id", () => {
    const state = {
      ...EMPTY_RUN_STATE,
      agents: [agent()],
    };
    const next = applyEvent(
      state,
      event(1, "agent.updated", {
        agent_id: "agent-a",
        iteration: 3,
        step: "at cap",
        session_url: undefined,
      }),
    );

    expect(next.agents[0]).toMatchObject({
      id: "agent-a",
      iteration: 3,
      step: "at cap",
      session_url: "https://app.devin.ai/sessions/a",
    });
    expect("agent_id" in next.agents[0]).toBe(false);
  });

  it("ignores agent.updated for an unknown agent", () => {
    const state = {
      ...EMPTY_RUN_STATE,
      agents: [agent()],
    };
    const next = applyEvent(
      state,
      event(1, "agent.updated", {
        agent_id: "agent-missing",
        iteration: 3,
      }),
    );

    expect(next.agents).toBe(state.agents);
  });

  it("sets finished_at when an agent reaches a terminal status", () => {
    const state = {
      ...EMPTY_RUN_STATE,
      agents: [agent()],
    };
    const finishedAt = "2025-01-01T00:03:00.000Z";
    const next = applyEvent(
      state,
      event(1, "agent.status_changed", {
        agent_id: "agent-a",
        status: "failed",
        previous_status: "working",
        finished_at: finishedAt,
      }),
    );

    expect(next.agents[0].finished_at).toBe(finishedAt);
  });
});
