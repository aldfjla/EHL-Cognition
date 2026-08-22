"use client";

/**
 * TEMPORARY LOCAL STUB — the real AgentOpsPanel is being built in the
 * agent-ops slice (which owns components/agents/**). This file exists only so
 * the run page composes and typechecks against the agreed interface:
 *
 *   { runId: string; agents: Agent[]; onFocusAgent?: (agentId: string) => void }
 *
 * It must be deleted (in favour of the real panel from `dev`) before this
 * branch's final push.
 */

import type { Agent } from "@/lib/types";

export interface AgentOpsPanelProps {
  runId: string;
  agents: Agent[];
  onFocusAgent?: (agentId: string) => void;
}

export default function AgentOpsPanel({ agents, onFocusAgent }: AgentOpsPanelProps) {
  return (
    <section className="stub">
      <div className="stub-label">Agent ops (stub)</div>
      <ul className="mt-2 space-y-1">
        {agents.map((agent) => (
          <li key={agent.id}>
            <button
              type="button"
              className="font-mono text-xs text-slate-400 hover:text-sky-400"
              onClick={() => onFocusAgent?.(agent.id)}
            >
              {agent.title} · {agent.status}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
