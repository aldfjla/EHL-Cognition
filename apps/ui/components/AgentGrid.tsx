"use client";

/**
 * Every live agent in the run, as a grid of cards.
 *
 * Ordered by creation, which is dispatch order, so the grid reads as the team
 * assembling: one Hardware Engineer, then Test Infra, then a burst of
 * Debugging Engineers when the suite comes back red. That burst is the moment
 * the demo lands, so the grid animates additions rather than snapping.
 */

import { useState } from "react";

import AgentCard from "./AgentCard";
import type { Agent } from "@/lib/types";

export interface AgentGridProps {
  agents: Agent[];
  /** Cluster id currently hovered elsewhere, for cross-highlighting. */
  activeClusterId?: string | null;
  /** Agent focused from the graph or a chat ref chip. */
  focusedAgentId?: string | null;
}

const FINISHED: Agent["status"][] = ["succeeded", "failed", "cancelled"];

function byCreation(a: Agent, b: Agent): number {
  return Date.parse(a.created_at) - Date.parse(b.created_at);
}

export default function AgentGrid({
  agents,
  activeClusterId = null,
  focusedAgentId = null,
}: AgentGridProps) {
  const [showFinished, setShowFinished] = useState(false);

  const isHighlighted = (agent: Agent): boolean =>
    agent.id === focusedAgentId ||
    (activeClusterId !== null && agent.cluster_id === activeClusterId);

  const ordered = agents.slice().sort(byCreation);
  const active = ordered.filter((a) => !FINISHED.includes(a.status));
  const finished = ordered.filter((a) => FINISHED.includes(a.status));

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="flex items-baseline gap-3">
        <div className="stub-label">
          The team · {agents.length} agent{agents.length === 1 ? "" : "s"}
        </div>
        {finished.length > 0 && (
          <button
            type="button"
            onClick={() => setShowFinished((open) => !open)}
            className="ml-auto font-mono text-[10px] text-slate-500 hover:text-slate-300"
          >
            {showFinished ? "hide" : "show"} {finished.length} finished
          </button>
        )}
      </div>

      {agents.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No agents dispatched yet.</p>
      ) : (
        <>
          {active.length > 0 && (
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {active.map((agent) => (
                <div key={agent.id} className="animate-rise">
                  <AgentCard agent={agent} highlighted={isHighlighted(agent)} />
                </div>
              ))}
            </div>
          )}

          {finished.length > 0 && showFinished && (
            <div className="mt-3 grid grid-cols-1 gap-3 opacity-70 sm:grid-cols-2 lg:grid-cols-3">
              {finished.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  highlighted={isHighlighted(agent)}
                />
              ))}
            </div>
          )}

          {active.length === 0 && !showFinished && (
            <p className="mt-2 text-sm text-slate-500">
              All {finished.length} agents finished.
            </p>
          )}
        </>
      )}
    </div>
  );
}
