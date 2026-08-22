"use client";

/**
 * The live counters header — the run's vital signs in one row.
 *
 * Every number is derived from upserted-by-id state (scenario list, agent
 * list), not by incrementing on events, so replays and reconnects can never
 * make a counter drift; `suite.progress` and `worker.pool_changed` refine what
 * they carry (queue depth, pool size) but never contradict the derived counts.
 */

import clsx from "clsx";

import type { Agent, Scenario, Stage } from "@/lib/types";

import type { WorkerPool } from "./liveState";
import { countScenarios, countWorkingAgents, effectivePool } from "./liveState";

export interface LiveCountersProps {
  stage: Stage | null;
  scenarios: Scenario[];
  agents: Agent[];
  pool: WorkerPool | null;
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className="flex min-w-[90px] flex-col items-center rounded border border-surface-border bg-surface px-3 py-2">
      <span className={clsx("font-mono text-xl font-semibold", tone ?? "text-slate-200")}>
        {value}
      </span>
      <span className="stub-label mt-0.5">{label}</span>
    </div>
  );
}

export default function LiveCounters({
  stage,
  scenarios,
  agents,
  pool,
}: LiveCountersProps) {
  const counts = countScenarios(scenarios);
  const working = countWorkingAgents(agents);
  const shownPool = effectivePool(pool, scenarios);

  return (
    <section className="rounded-lg border border-surface-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Counter
          label="running"
          value={counts.running}
          tone={counts.running > 0 ? "text-status-running" : undefined}
        />
        <Counter label="queued" value={counts.pending} tone="text-status-pending" />
        <Counter
          label="passed"
          value={counts.passed}
          tone={counts.passed > 0 ? "text-status-passed" : undefined}
        />
        <Counter
          label="failed"
          value={counts.failed + counts.error}
          tone={counts.failed + counts.error > 0 ? "text-status-failed" : undefined}
        />
        <Counter
          label="agents working"
          value={working}
          tone={working > 0 ? "text-status-running" : undefined}
        />
        <Counter
          label="workers"
          value={shownPool ? `${shownPool.busy}/${shownPool.workers}` : "—"}
          tone={
            shownPool && shownPool.workers > 0 && shownPool.busy === shownPool.workers
              ? "text-status-blocked"
              : undefined
          }
        />

        <div className="ml-auto flex flex-col items-end gap-1">
          <span className="font-mono text-xs uppercase tracking-widest text-status-running">
            {stage ?? "—"}
          </span>
          {shownPool?.reason && (
            <span className="font-mono text-[10px] text-slate-500">
              pool: {shownPool.reason}
            </span>
          )}
          {shownPool && shownPool.queued > 0 && (
            <span className="font-mono text-[10px] text-slate-500">
              {shownPool.queued} in queue
            </span>
          )}
        </div>
      </div>
    </section>
  );
}
