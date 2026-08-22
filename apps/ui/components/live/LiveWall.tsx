"use client";

/**
 * The live simulation wall — one tile per scenario that is (or was) running.
 *
 * Stream budget: only tiles that are on screen may hold an MJPEG connection,
 * and never more than MAX_STREAMS at once; everything else falls back to slow
 * still-frame polling inside `LiveFeed`. The wall stays live regardless,
 * because the throttled `scenario.progress` events drive the progress rails
 * without any video connection at all.
 *
 * Running scenarios lead the grid; freshly finished ones stay, transitioned to
 * pass/fail with their recorded video, so an outcome is seen rather than
 * inferred from a tile disappearing. Clicking a tile opens the focus overlay
 * with params, criteria and diagnosis.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import type { Scenario } from "@/lib/types";

import type { LiveState } from "./liveState";
import { mergeLive } from "./liveState";
import LiveTile from "./LiveTile";
import LiveTileFocus from "./LiveTileFocus";

/** Hard cap on simultaneous MJPEG connections, regardless of viewport size. */
const MAX_STREAMS = 8;

export interface LiveWallProps {
  runId: string;
  scenarios: Scenario[];
  live: LiveState;
  /** Scripted replay — tiles draw synthetic feeds instead of fetching. */
  synthetic: boolean;
}

function wallOrder(a: Scenario, b: Scenario): number {
  const rank = (s: Scenario): number =>
    s.status === "running" ? 0 : s.status === "failed" || s.status === "error" ? 1 : s.status === "passed" ? 2 : 3;
  const byRank = rank(a) - rank(b);
  return byRank !== 0 ? byRank : a.index - b.index;
}

export default function LiveWall({ runId, scenarios, live, synthetic }: LiveWallProps) {
  const [focusedId, setFocusedId] = useState<string | null>(null);
  // Visibility lives in a ref + version counter: tiles report in and out as
  // the user scrolls, and we only need it when computing the stream budget.
  const visibleRef = useRef<Set<string>>(new Set());
  const [visibleVersion, setVisibleVersion] = useState(0);

  const onVisibility = useCallback((scenarioId: string, visible: boolean) => {
    const set = visibleRef.current;
    const had = set.has(scenarioId);
    if (visible === had) return;
    if (visible) set.add(scenarioId);
    else set.delete(scenarioId);
    setVisibleVersion((v) => v + 1);
  }, []);

  const merged = useMemo(
    () =>
      scenarios
        .filter((s) => s.status !== "pending")
        .map((s) => mergeLive(s, live))
        .sort(wallOrder),
    [scenarios, live],
  );

  // The stream budget: visible, running tiles, in wall order, capped.
  const streamingIds = useMemo(() => {
    const ids = new Set<string>();
    for (const scenario of merged) {
      if (ids.size >= MAX_STREAMS) break;
      if (scenario.status !== "running") continue;
      if (scenario.live_frame_path === null) continue;
      if (!visibleRef.current.has(scenario.id)) continue;
      ids.add(scenario.id);
    }
    return ids;
    // visibleVersion stands in for visibleRef.current (mutated in onVisibility)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merged, visibleVersion]);

  const focused = focusedId
    ? (merged.find((s) => s.id === focusedId) ?? null)
    : null;

  if (merged.length === 0) {
    return (
      <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
        <div className="stub-label">Live simulations</div>
        <p className="mt-2 text-sm text-slate-400">
          Nothing simulating yet. Tiles appear here the moment workers pick up
          scenarios.
        </p>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <div className="stub-label">Live simulations</div>
        <div className="font-mono text-[10px] text-slate-500">
          {merged.filter((s) => s.status === "running").length} running ·{" "}
          {streamingIds.size} streaming
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
        {merged.map((scenario) => (
          <LiveTile
            key={scenario.id}
            runId={runId}
            scenario={scenario}
            streaming={streamingIds.has(scenario.id)}
            synthetic={synthetic}
            onVisibility={onVisibility}
            onFocus={setFocusedId}
          />
        ))}
      </div>

      {focused && (
        <LiveTileFocus
          runId={runId}
          scenario={focused}
          synthetic={synthetic}
          onClose={() => setFocusedId(null)}
        />
      )}
    </section>
  );
}
