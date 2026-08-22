"use client";

/**
 * Every live agent in the run, as a grid of cards.
 *
 * Ordered by creation, which is dispatch order, so the grid reads as the team
 * assembling: one Hardware Engineer, then Test Infra, then a burst of
 * Debugging Engineers when the suite comes back red. That burst is the moment
 * the demo lands, so the grid animates additions rather than snapping.
 */

import AgentCard from "./AgentCard";
import type { Agent } from "@/lib/types";

export interface AgentGridProps {
  agents: Agent[];
  /** Cluster id currently hovered elsewhere, for cross-highlighting. */
  activeClusterId?: string | null;
}

export default function AgentGrid({ agents, activeClusterId = null }: AgentGridProps) {
  // TODO(build): group by lifecycle (active first, finished collapsed), and
  // animate entry so a fan-out of six agents is visibly a fan-out.
  return (
    <div className="stub">
      <div className="stub-label">The team · {agents.length} agents</div>
      {agents.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No agents dispatched yet.</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-3">
          {agents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              highlighted={
                activeClusterId !== null && agent.cluster_id === activeClusterId
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}
