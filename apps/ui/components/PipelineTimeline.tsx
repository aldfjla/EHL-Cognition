"use client";

/**
 * Stage progress rail — where the run is, top to bottom.
 *
 * Renders STAGE_ORDER as a vertical rail with the current stage highlighted,
 * completed stages dimmed, and the terminal state called out. The one component
 * that answers "how far along is this?" at a glance from across a room.
 */

import { STAGE_ORDER, type Run, type Stage } from "@/lib/types";

export interface PipelineTimelineProps {
  /** Current stage, or null before the run object arrives. */
  stage: Stage | null;
  /** Full run, for the terminal-state summary at the foot of the rail. */
  run: Run | null;
}

export default function PipelineTimeline({ stage, run }: PipelineTimelineProps) {
  // TODO(build): mark stages before `stage` as done, the current one as active
  // with a pulse, and fan-out stages (INVESTIGATE, FIX) with their agent count.
  return (
    <div className="stub">
      <div className="stub-label">Pipeline</div>
      <ol className="mt-3 space-y-1 font-mono text-xs">
        {STAGE_ORDER.map((s) => (
          <li
            key={s}
            className={s === stage ? "text-status-running" : "text-slate-500"}
          >
            {s === stage ? "▶ " : "  "}
            {s}
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-slate-500">{run?.stage ?? "awaiting run"}</p>
    </div>
  );
}
