"use client";

/**
 * One agent: role, current task, iteration, status, and a link to its real
 * Devin session.
 *
 * The session link matters more than it looks — it is what turns "the demo
 * claims agents ran" into "here is the session, click it".
 */

import { ROLE_LABELS, type Agent } from "@/lib/types";

export interface AgentCardProps {
  agent: Agent;
  /** Highlight when this agent owns the cluster the user is hovering. */
  highlighted?: boolean;
}

export default function AgentCard({ agent, highlighted = false }: AgentCardProps) {
  // TODO(build): status dot coloured from tailwind `status.*`, live activity
  // ticker from agent.last_activity, iteration pips (2/3), elapsed timer.
  return (
    <div className="stub" data-highlighted={highlighted}>
      <div className="stub-label">{ROLE_LABELS[agent.role]}</div>
      <p className="mt-1 truncate text-sm text-slate-300">{agent.title || agent.id}</p>
      <p className="mt-1 line-clamp-2 text-xs text-slate-500">{agent.task}</p>
      <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-500">
        <span>{agent.status}</span>
        <span>
          {agent.iteration}/{agent.max_iterations}
        </span>
      </div>
    </div>
  );
}
