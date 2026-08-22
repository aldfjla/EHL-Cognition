"use client";

/**
 * The N×M pass/fail grid — the evidence layer.
 *
 * One cell per randomized world, coloured by status, filling in live as the
 * suite runs. Cells are grouped by cluster once CLUSTER_FAILURES completes, so
 * the visual shift from "scattered red" to "three blocks of red" *is* the
 * clustering step happening in front of the audience.
 *
 * Hovering a cell shows its seed, params and diagnosis; clicking it opens that
 * scenario's video.
 */

import clsx from "clsx";
import { useEffect, useState } from "react";

import type { Cluster, Scenario, ScenarioStatus } from "@/lib/types";

export interface ScenarioMatrixProps {
  scenarios: Scenario[];
  clusters: Cluster[];
  /** Externally focused scenario, e.g. from a chat message's ref chip. */
  selectedScenarioId?: string | null;
  onSelectScenario?: (scenarioId: string) => void;
  onHoverCluster?: (clusterId: string | null) => void;
}

const CELL_TONE: Record<ScenarioStatus, string> = {
  pending: "bg-slate-800 border border-surface-border",
  running: "bg-status-running/70 animate-pulse",
  passed: "bg-status-passed",
  failed: "bg-status-failed",
  error: "bg-status-error",
};

function formatParams(scenario: Scenario): string {
  return Object.entries(scenario.params)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join("  ");
}

function tooltip(scenario: Scenario): string {
  const lines = [
    `#${scenario.index} · seed ${scenario.seed} · ${scenario.status}`,
    scenario.label,
    formatParams(scenario),
  ];
  if (scenario.diagnosis) lines.push("", scenario.diagnosis);
  if (scenario.error) lines.push("", `sim error: ${scenario.error}`);
  return lines.filter(Boolean).join("\n");
}

function Cell({
  scenario,
  onSelect,
}: {
  scenario: Scenario;
  onSelect?: (scenarioId: string) => void;
}) {
  const settled = scenario.status !== "pending" && scenario.status !== "running";
  return (
    <button
      type="button"
      title={tooltip(scenario)}
      aria-label={tooltip(scenario)}
      onClick={() => onSelect?.(scenario.id)}
      className={clsx(
        "aspect-square w-full rounded-sm transition-transform hover:scale-110 focus:outline-none focus:ring-1 focus:ring-sky-400",
        CELL_TONE[scenario.status],
        settled && "animate-land",
      )}
    />
  );
}

export default function ScenarioMatrix({
  scenarios,
  clusters,
  selectedScenarioId = null,
  onSelectScenario,
  onHoverCluster,
}: ScenarioMatrixProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Follow the parent when something else on the page focuses a scenario.
  useEffect(() => {
    if (selectedScenarioId !== null) setSelectedId(selectedScenarioId);
  }, [selectedScenarioId]);

  const selected = scenarios.find((s) => s.id === selectedId) ?? null;

  const passed = scenarios.filter((s) => s.status === "passed").length;
  const failed = scenarios.filter((s) => s.status === "failed").length;
  const errored = scenarios.filter((s) => s.status === "error").length;
  const running = scenarios.filter((s) => s.status === "running").length;

  const ordered = scenarios.slice().sort((a, b) => a.index - b.index);
  const clustered = clusters.length > 0;
  const clusteredIds = new Set(
    clusters.flatMap((cluster) => cluster.scenario_ids),
  );
  const unclustered = ordered.filter((s) => !clusteredIds.has(s.id));

  const select = (scenarioId: string): void => {
    setSelectedId(scenarioId);
    onSelectScenario?.(scenarioId);
  };

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">
        Scenario matrix · {passed} passed · {failed} failed
        {errored > 0 ? ` · ${errored} sim error` : ""}
        {running > 0 ? ` · ${running} running` : ""} · {clusters.length} clusters
      </div>

      {scenarios.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">Suite has not started.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {!clustered ? (
            <div className="grid grid-cols-8 gap-1 sm:grid-cols-12">
              {ordered.map((scenario) => (
                <Cell key={scenario.id} scenario={scenario} onSelect={select} />
              ))}
            </div>
          ) : (
            <>
              {clusters.map((cluster) => {
                const cells = cluster.scenario_ids
                  .map((id) => ordered.find((s) => s.id === id))
                  .filter((s): s is Scenario => s !== undefined);
                if (cells.length === 0) return null;
                return (
                  <div
                    key={cluster.id}
                    onMouseEnter={() => onHoverCluster?.(cluster.id)}
                    onMouseLeave={() => onHoverCluster?.(null)}
                    className="rounded-md border border-status-failed/40 p-2"
                  >
                    <div className="font-mono text-[10px] uppercase tracking-widest text-status-failed">
                      {cluster.label} · {cluster.size}
                    </div>
                    <div className="mt-2 grid grid-cols-8 gap-1 sm:grid-cols-12">
                      {cells.map((scenario) => (
                        <Cell
                          key={scenario.id}
                          scenario={scenario}
                          onSelect={select}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}

              {unclustered.length > 0 && (
                <div
                  onMouseEnter={() => onHoverCluster?.(null)}
                  className="rounded-md border border-surface-border p-2"
                >
                  <div className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
                    unclustered · {unclustered.length}
                  </div>
                  <div className="mt-2 grid grid-cols-8 gap-1 sm:grid-cols-12">
                    {unclustered.map((scenario) => (
                      <Cell key={scenario.id} scenario={scenario} onSelect={select} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {selected && (
        <div className="mt-3 border-t border-surface-border pt-3">
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-mono text-xs text-slate-300">
              #{selected.index} · seed {selected.seed} · {selected.status}
            </span>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="font-mono text-[10px] text-slate-500 hover:text-slate-300"
            >
              close
            </button>
          </div>
          <p className="mt-1 text-xs text-slate-400">{selected.label}</p>
          <p className="mt-1 break-words font-mono text-[10px] text-slate-500">
            {formatParams(selected)}
          </p>
          {selected.diagnosis && (
            <p className="mt-2 text-xs text-status-failed">{selected.diagnosis}</p>
          )}
          {selected.criteria.length > 0 && (
            <ul className="mt-2 space-y-0.5 font-mono text-[10px]">
              {selected.criteria.map((criterion) => (
                <li
                  key={criterion.id}
                  className={
                    criterion.passed ? "text-status-passed" : "text-status-failed"
                  }
                >
                  {criterion.passed ? "pass" : "fail"} {criterion.id}
                  {criterion.value !== null && criterion.value !== undefined
                    ? ` · ${criterion.value}`
                    : ""}
                  {criterion.threshold !== null && criterion.threshold !== undefined
                    ? ` / ${criterion.threshold}`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
