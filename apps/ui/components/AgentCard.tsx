"use client";

/**
 * One agent: role, current task, iteration, status, and a link to its real
 * Devin session.
 *
 * The session link matters more than it looks — it is what turns "the demo
 * claims agents ran" into "here is the session, click it".
 */

import clsx from "clsx";
import { useEffect, useState } from "react";

import { ROLE_LABELS, type Agent, type AgentStatus } from "@/lib/types";

export interface AgentCardProps {
  agent: Agent;
  /** Highlight when this agent owns the cluster the user is hovering. */
  highlighted?: boolean;
}

/** Status → dot colour and border tint. Named by meaning, never by hue. */
const STATUS_TONE: Record<AgentStatus, { dot: string; text: string }> = {
  queued: { dot: "bg-status-pending", text: "text-slate-400" },
  starting: { dot: "bg-status-running/60 animate-pulse", text: "text-status-running" },
  working: { dot: "bg-status-running animate-pulse", text: "text-status-running" },
  blocked: { dot: "bg-status-blocked", text: "text-status-blocked" },
  succeeded: { dot: "bg-status-passed", text: "text-status-passed" },
  failed: { dot: "bg-status-failed", text: "text-status-failed" },
  cancelled: { dot: "bg-status-pending", text: "text-slate-500" },
};

const LIVE_STATUSES: AgentStatus[] = ["queued", "starting", "working", "blocked"];

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/**
 * Wall-clock time the agent has been alive. Ticks only while the agent is
 * unfinished — a frozen timer on a finished card is information, not a bug.
 */
function useElapsed(agent: Agent): string {
  const started = Date.parse(agent.created_at);
  const finished = agent.finished_at ? Date.parse(agent.finished_at) : null;
  const live = finished === null && LIVE_STATUSES.includes(agent.status);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  if (Number.isNaN(started)) return "—";
  return formatElapsed((finished ?? (live ? now : started)) - started);
}

export default function AgentCard({ agent, highlighted = false }: AgentCardProps) {
  const elapsed = useElapsed(agent);
  const tone = STATUS_TONE[agent.status];
  const pips = Array.from({ length: Math.max(1, agent.max_iterations) });

  return (
    <div
      data-highlighted={highlighted}
      className={clsx(
        "flex min-w-0 flex-col rounded-lg border bg-surface-raised p-3 transition-colors",
        highlighted
          ? "border-status-running shadow-[0_0_0_1px_rgba(56,189,248,0.4)]"
          : "border-surface-border",
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className={clsx("h-2 w-2 shrink-0 rounded-full", tone.dot)} />
        <span className="stub-label truncate">{ROLE_LABELS[agent.role]}</span>
        <span className="ml-auto font-mono text-[10px] text-slate-500">{elapsed}</span>
      </div>

      <p className="mt-1.5 truncate text-sm text-slate-200" title={agent.title}>
        {agent.title || agent.id}
      </p>
      <p className="mt-1 line-clamp-2 text-xs text-slate-500" title={agent.task}>
        {agent.task}
      </p>

      {agent.last_activity && (
        <p
          className="mt-2 truncate border-l border-surface-border pl-2 font-mono text-[10px] text-slate-400"
          title={agent.last_activity}
        >
          {agent.last_activity}
        </p>
      )}

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className={clsx("font-mono text-[10px] uppercase", tone.text)}>
          {agent.status}
        </span>
        <span
          className="flex items-center gap-1"
          title={`iteration ${agent.iteration}/${agent.max_iterations}`}
        >
          {pips.map((_, i) => (
            <span
              key={i}
              aria-hidden
              className={clsx(
                "h-1.5 w-1.5 rounded-full",
                i < agent.iteration ? "bg-status-running" : "bg-surface-border",
              )}
            />
          ))}
          <span className="ml-1 font-mono text-[10px] text-slate-500">
            {agent.iteration}/{agent.max_iterations}
          </span>
        </span>
      </div>

      {agent.session_url ? (
        <a
          href={agent.session_url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 font-mono text-[10px] text-sky-400 hover:underline"
        >
          open Devin session ↗
        </a>
      ) : (
        <span className="mt-2 font-mono text-[10px] text-slate-600">
          session pending
        </span>
      )}
    </div>
  );
}
