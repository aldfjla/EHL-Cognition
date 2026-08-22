"use client";

/**
 * A standalone harness for the agent operations panel.
 *
 * Mission control mounts `AgentOpsPanel` inside the run page; this route exists
 * so the panel, its fallbacks and the desktop wall can be exercised on their
 * own with no API, no WebSocket and no real Devin sessions — including the
 * cases that are hard to reach on a live run (an agent with no desktop, a
 * blocked agent, a failed attempt).
 *
 * It applies the scripted `agent.*` events **incrementally**, at the scripted
 * pace, on purpose: a static roster would prove nothing about how the panel
 * behaves while agents appear and patch themselves in.
 */

import { useEffect, useReducer, useState } from "react";

import AgentOpsPanel from "@/components/agents/AgentOpsPanel";
import { MOCK_RUN_ID, replayMockRun } from "@/lib/mockRun";
import type { Agent, TypedRunEvent } from "@/lib/types";

/** A replay event, or the harness restarting the replay from the top. */
type RosterAction = TypedRunEvent | { type: "harness.reset" };

/** Fold the agent-related events of the replay into a roster. */
function rosterReducer(agents: Agent[], event: RosterAction): Agent[] {
  switch (event.type) {
    case "harness.reset":
      return [];

    case "agent.created":
      return agents.some((a) => a.id === event.data.id)
        ? agents
        : [...agents, event.data];

    case "agent.updated": {
      const { agent_id: id, ...patch } = event.data;
      return agents.map((a) => (a.id === id ? { ...a, ...patch } : a));
    }

    case "agent.status_changed":
      return agents.map((a) =>
        a.id === event.data.agent_id ? { ...a, status: event.data.status } : a,
      );

    case "agent.activity":
      return agents.map((a) =>
        a.id === event.data.agent_id ? { ...a, last_activity: event.data.text } : a,
      );

    default:
      return agents;
  }
}

const SPEEDS: { label: string; value: number }[] = [
  { label: "1x", value: 1 },
  { label: "4x", value: 0.25 },
  { label: "instant", value: 0 },
];

export default function AgentsDemoPage() {
  const [speed, setSpeed] = useState<number>(0.25);
  const [agents, apply] = useReducer(rosterReducer, [] as Agent[]);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    // `generation` restarts the replay; the roster resets with it so a restart
    // is a restart rather than a second team layered on the first.
    apply({ type: "harness.reset" });
    return replayMockRun(MOCK_RUN_ID, apply, { speed });
  }, [speed, generation]);

  return (
    <main className="space-y-4 p-6">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="font-mono text-lg font-semibold">agent operations — harness</h1>
        <span className="rounded border border-sky-700 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-sky-300">
          replay
        </span>
        <span className="stub-label">{agents.length} agents so far</span>
        <div className="ml-auto flex items-center gap-2">
          {SPEEDS.map((option) => (
            <button
              key={option.label}
              type="button"
              aria-pressed={option.value === speed}
              onClick={() => setSpeed(option.value)}
              className={
                option.value === speed
                  ? "rounded border border-status-running px-2 py-0.5 font-mono text-[10px] uppercase text-status-running"
                  : "rounded border border-surface-border px-2 py-0.5 font-mono text-[10px] uppercase text-slate-500"
              }
            >
              {option.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setGeneration((n) => n + 1)}
            className="rounded border border-surface-border px-2 py-0.5 font-mono text-[10px] uppercase text-slate-400"
          >
            restart
          </button>
        </div>
      </header>

      <AgentOpsPanel runId={MOCK_RUN_ID} agents={agents} />
    </main>
  );
}
