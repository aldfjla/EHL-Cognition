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

import type { Cluster, Scenario } from "@/lib/types";

export interface ScenarioMatrixProps {
  scenarios: Scenario[];
  clusters: Cluster[];
  onSelectScenario?: (scenarioId: string) => void;
  onHoverCluster?: (clusterId: string | null) => void;
}

export default function ScenarioMatrix({ scenarios, clusters }: ScenarioMatrixProps) {
  // TODO(build): square grid sized to scenario count, cell colour from
  // tailwind `status.*`, tooltip with seed/params/diagnosis, cluster grouping
  // with a labelled border once clusters arrive.
  const passed = scenarios.filter((s) => s.status === "passed").length;
  const failed = scenarios.filter((s) => s.status === "failed").length;

  return (
    <div className="stub">
      <div className="stub-label">
        Scenario matrix · {passed} passed · {failed} failed · {clusters.length}{" "}
        clusters
      </div>
      {scenarios.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">Suite has not started.</p>
      ) : (
        <div className="mt-3 grid grid-cols-8 gap-1">
          {scenarios.map((s) => (
            <div
              key={s.id}
              title={`seed ${s.seed} · ${s.status}`}
              className="aspect-square rounded-sm bg-slate-700"
            />
          ))}
        </div>
      )}
    </div>
  );
}
