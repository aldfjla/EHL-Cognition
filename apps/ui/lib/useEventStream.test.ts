import { describe, expect, it } from "vitest";

import {
  EMPTY_RUN_STATE,
  acceptEventSeq,
  applyEvent,
  beginResync,
  completeResync,
  recordResyncStart,
  resetResyncTracker,
  type EventCursor,
  type ResyncTracker,
} from "./useEventStream";

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

describe("event stream sequencing", () => {
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
});
