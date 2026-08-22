"use client";

/**
 * Who is talking to whom — the communication graph.
 *
 * Nodes are agents (positioned by role), edges are relayed messages, edge
 * weight is traffic volume. The shape itself is the point: a hub through the
 * orchestrator, with fan-out clusters of Investigators and Fixers hanging off
 * it, is a picture of the architecture that no paragraph explains as fast.
 *
 * Drawn as inline SVG rather than a graph library — a dozen nodes with fixed
 * role-based positions does not need a force simulation, and a dependency that
 * needs a canvas is a dependency that can fail on stage.
 */

import type { Agent, Message } from "@/lib/types";

export interface AgentGraphProps {
  agents: Agent[];
  messages: Message[];
  /** Called when a node is clicked, to focus that agent elsewhere. */
  onSelectAgent?: (agentId: string) => void;
}

export default function AgentGraph({ agents, messages }: AgentGraphProps) {
  // TODO(build): fixed layout — orchestrator centre, roles on a ring by stage
  // order, fan-out agents on an outer arc. Edge opacity from message count,
  // and a brief pulse along the edge when a new message arrives.
  return (
    <div className="stub">
      <div className="stub-label">
        Communication graph · {agents.length} nodes · {messages.length} edges
      </div>
      <svg
        viewBox="0 0 360 200"
        className="mt-3 h-[200px] w-full"
        role="img"
        aria-label="Agent communication graph placeholder"
      >
        <circle cx="180" cy="100" r="6" className="fill-slate-600" />
        <text
          x="180"
          y="124"
          textAnchor="middle"
          className="fill-slate-500 font-mono text-[9px]"
        >
          orchestrator
        </text>
      </svg>
    </div>
  );
}
