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

import { useEffect, useState } from "react";

import { ROLE_LABELS, type Agent, type Message, type Role } from "@/lib/types";

export interface AgentGraphProps {
  agents: Agent[];
  messages: Message[];
  /** Called when a node is clicked, to focus that agent elsewhere. */
  onSelectAgent?: (agentId: string) => void;
}

const WIDTH = 360;
const HEIGHT = 260;
const CENTRE = { x: WIDTH / 2, y: HEIGHT / 2 };
const HUB = "orchestrator";

/** Ring position per role, in pipeline order clockwise from the top. */
const ROLE_ANGLE: Record<Role, number> = {
  modeler: -90,
  harness_builder: -39,
  scenario_designer: 12,
  investigator: 63,
  fixer: 114,
  reviewer: 165,
  reporter: 216,
};

const RING_RADIUS = 78;
/** Fan-out agents sit on an outer arc so a burst of six reads as a burst. */
const FANOUT_RADIUS = 108;

interface Node {
  id: string;
  label: string;
  sublabel: string;
  x: number;
  y: number;
  status: Agent["status"] | "hub";
}

function polar(angleDeg: number, radius: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: CENTRE.x + Math.cos(rad) * radius,
    y: CENTRE.y + Math.sin(rad) * radius,
  };
}

const STATUS_FILL: Record<Agent["status"] | "hub", string> = {
  hub: "fill-slate-400",
  queued: "fill-status-pending",
  starting: "fill-status-running",
  working: "fill-status-running",
  blocked: "fill-status-blocked",
  succeeded: "fill-status-passed",
  failed: "fill-status-failed",
  cancelled: "fill-status-pending",
};

function layout(agents: Agent[]): Map<string, Node> {
  const nodes = new Map<string, Node>();
  nodes.set(HUB, {
    id: HUB,
    label: "orchestrator",
    sublabel: "relays every exchange",
    x: CENTRE.x,
    y: CENTRE.y,
    status: "hub",
  });

  const byRole = new Map<Role, Agent[]>();
  for (const agent of agents) {
    const bucket = byRole.get(agent.role) ?? [];
    bucket.push(agent);
    byRole.set(agent.role, bucket);
  }

  for (const [role, roleAgents] of byRole) {
    const base = ROLE_ANGLE[role];
    const count = roleAgents.length;
    roleAgents.forEach((agent, i) => {
      // One agent sits on the ring; siblings spread along an outer arc.
      const spread = count === 1 ? 0 : (i - (count - 1) / 2) * 18;
      const radius = count === 1 ? RING_RADIUS : FANOUT_RADIUS;
      const { x, y } = polar(base + spread, radius);
      nodes.set(agent.id, {
        id: agent.id,
        label: ROLE_LABELS[role],
        sublabel: agent.title || agent.id,
        x,
        y,
        status: agent.status,
      });
    });
  }

  return nodes;
}

interface Edge {
  key: string;
  from: Node;
  to: Node;
  count: number;
  latest: string;
}

function buildEdges(
  messages: Message[],
  nodes: Map<string, Node>,
  agents: Agent[],
): Edge[] {
  const resolve = (agentId: string | null, role: string): Node => {
    if (agentId !== null) {
      const direct = nodes.get(agentId);
      if (direct) return direct;
    }
    if (role !== HUB && role !== "broadcast") {
      const first = agents.find((agent) => agent.role === role);
      const viaRole = first ? nodes.get(first.id) : undefined;
      if (viaRole) return viaRole;
    }
    return nodes.get(HUB) as Node;
  };

  const edges = new Map<string, Edge>();
  for (const message of messages) {
    const from = resolve(message.from_agent_id, message.from_role);
    const to = resolve(message.to_agent_id, message.to_role);
    if (from.id === to.id) continue;
    const key = `${from.id}->${to.id}`;
    const existing = edges.get(key);
    if (existing) {
      existing.count += 1;
      existing.latest = message.id;
    } else {
      edges.set(key, { key, from, to, count: 1, latest: message.id });
    }
  }
  return [...edges.values()];
}

export default function AgentGraph({
  agents,
  messages,
  onSelectAgent,
}: AgentGraphProps) {
  const nodes = layout(agents);
  const edges = buildEdges(messages, nodes, agents);
  const newest = messages.length > 0 ? messages[messages.length - 1].id : null;
  const [pulsing, setPulsing] = useState<string | null>(null);

  // A brief pulse along the edge that just carried a relay.
  useEffect(() => {
    if (newest === null) return;
    setPulsing(newest);
    const timer = setTimeout(() => setPulsing(null), 1200);
    return () => clearTimeout(timer);
  }, [newest]);

  const maxCount = edges.reduce((max, edge) => Math.max(max, edge.count), 1);

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">
        Communication graph · {nodes.size} nodes · {edges.length} edges
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mt-3 w-full"
        role="img"
        aria-label={`Agent communication graph: ${nodes.size} nodes, ${edges.length} edges`}
      >
        {edges.map((edge) => {
          const active = pulsing !== null && edge.latest === pulsing;
          return (
            <g key={edge.key}>
              <line
                x1={edge.from.x}
                y1={edge.from.y}
                x2={edge.to.x}
                y2={edge.to.y}
                strokeWidth={1 + (edge.count / maxCount) * 2}
                className={active ? "stroke-sky-300" : "stroke-slate-600"}
                strokeOpacity={active ? 1 : 0.25 + (edge.count / maxCount) * 0.5}
              />
              {active && (
                <line
                  x1={edge.from.x}
                  y1={edge.from.y}
                  x2={edge.to.x}
                  y2={edge.to.y}
                  strokeWidth={4}
                  strokeOpacity={0.35}
                  className="animate-pulse stroke-sky-300"
                />
              )}
            </g>
          );
        })}

        {[...nodes.values()].map((node) => {
          const isHub = node.id === HUB;
          return (
            <g
              key={node.id}
              onClick={() => (isHub ? undefined : onSelectAgent?.(node.id))}
              className={isHub ? undefined : "cursor-pointer"}
            >
              <title>{`${node.label} — ${node.sublabel}`}</title>
              <circle
                cx={node.x}
                cy={node.y}
                r={isHub ? 8 : 6}
                className={STATUS_FILL[node.status]}
              />
              {(node.status === "working" || node.status === "starting") && (
                <circle
                  cx={node.x}
                  cy={node.y}
                  r={11}
                  fillOpacity={0}
                  strokeWidth={1}
                  className="animate-pulse stroke-status-running"
                />
              )}
              <text
                x={node.x}
                y={node.y + (node.y > CENTRE.y ? 18 : -11)}
                textAnchor="middle"
                className="fill-slate-400 font-mono text-[8px]"
              >
                {isHub ? "orchestrator" : node.label}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-[11px] leading-4 text-slate-500">
        Sessions cannot address each other. Every edge is the orchestrator
        writing one agent&apos;s finding into another agent&apos;s prompt.
      </p>
    </div>
  );
}
