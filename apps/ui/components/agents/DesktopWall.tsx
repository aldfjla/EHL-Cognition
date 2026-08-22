"use client";

/**
 * Every watchable machine at once.
 *
 * Tiles are laid out in attention order, so the blocked agent is top-left
 * rather than somewhere in the third row. Agents with no embeddable view still
 * get a tile — with their ticker instead of a frame — because a hole in the
 * wall reads as a crash.
 */

import AgentDesktop from "./AgentDesktop";
import type { PaneBlock } from "./agentOps";
import type { Agent } from "@/lib/types";

export interface DesktopWallProps {
  /** Agents to tile, already in the order they should appear. */
  agents: Agent[];
  paneFor: (agentId: string) => { live: boolean; reason: PaneBlock | null };
  onFocus?: (agentId: string) => void;
  onVisibilityChange?: (agentId: string, visible: boolean) => void;
}

export default function DesktopWall({
  agents,
  paneFor,
  onFocus,
  onVisibilityChange,
}: DesktopWallProps) {
  if (agents.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-surface-border p-4 text-sm text-slate-500">
        No agent is exposing a desktop right now.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {agents.map((agent) => {
        const pane = paneFor(agent.id);
        return (
          <AgentDesktop
            key={agent.id}
            agent={agent}
            live={pane.live}
            reason={pane.reason}
            size="tile"
            onFocus={onFocus}
            onVisibilityChange={onVisibilityChange}
          />
        );
      })}
    </div>
  );
}
