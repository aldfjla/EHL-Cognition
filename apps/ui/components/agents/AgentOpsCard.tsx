"use client";

/**
 * One agent, in operational detail: what we asked it to do, what the simulation
 * measured, where it is in its own work, and how to watch it.
 *
 * The two-block split in the middle is the point of this card. `task` is our
 * instruction and `issue` is the oracle's diagnosis, and they are labelled and
 * styled differently so that an agent's assignment can never be read as a
 * measured fact.
 */

import clsx from "clsx";

import AgentDesktop from "./AgentDesktop";
import { canEmbedDesktop, isFinished, type PaneBlock } from "./agentOps";
import { ROLE_LABELS, type Agent, type AgentStatus } from "@/lib/types";

export interface AgentOpsCardProps {
  agent: Agent;
  /** Cluster label for `agent.cluster_id`, when the run has one. */
  clusterLabel?: string | null;
  /** Is the inline desktop pane open? */
  expanded: boolean;
  onToggleExpanded: (agentId: string) => void;
  onFocus?: (agentId: string) => void;
  /** Pane decision for the inline desktop, from the panel's live budget. */
  paneLive?: boolean;
  paneReason?: PaneBlock | null;
  onVisibilityChange?: (agentId: string, visible: boolean) => void;
  dense?: boolean;
}

const STATUS_TONE: Record<AgentStatus, { dot: string; text: string }> = {
  queued: { dot: "bg-status-pending", text: "text-slate-400" },
  starting: { dot: "bg-status-running/60 animate-pulse", text: "text-status-running" },
  working: { dot: "bg-status-running animate-pulse", text: "text-status-running" },
  blocked: { dot: "bg-status-blocked", text: "text-status-blocked" },
  succeeded: { dot: "bg-status-passed", text: "text-status-passed" },
  failed: { dot: "bg-status-failed", text: "text-status-failed" },
  cancelled: { dot: "bg-status-pending", text: "text-slate-500" },
};

function scenarioSummary(ids: string[]): string {
  if (ids.length === 0) return "none in scope";
  if (ids.length <= 4) return ids.join(" ");
  return `${ids.slice(0, 4).join(" ")} +${ids.length - 4}`;
}

export default function AgentOpsCard({
  agent,
  clusterLabel = null,
  expanded,
  onToggleExpanded,
  onFocus,
  paneLive = false,
  paneReason = null,
  onVisibilityChange,
  dense = false,
}: AgentOpsCardProps) {
  const tone = STATUS_TONE[agent.status];
  const finished = isFinished(agent);
  const atCap = agent.max_iterations > 0 && agent.iteration >= agent.max_iterations;
  const embeddable = canEmbedDesktop(agent);
  const attention = agent.status === "blocked" || agent.status === "failed";

  return (
    <div
      data-agent-id={agent.id}
      className={clsx(
        "flex min-w-0 flex-col rounded-lg border bg-surface-raised p-3",
        attention
          ? agent.status === "blocked"
            ? "border-status-blocked/70"
            : "border-status-failed/70"
          : "border-surface-border",
        finished && "opacity-80",
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className={clsx("h-2 w-2 shrink-0 rounded-full", tone.dot)} />
        <span className="stub-label truncate">{ROLE_LABELS[agent.role]}</span>
        <span className={clsx("ml-auto shrink-0 font-mono text-[10px] uppercase", tone.text)}>
          {agent.status}
        </span>
      </div>

      <p className="mt-1.5 truncate text-sm text-slate-100" title={agent.title}>
        {agent.title || agent.id}
      </p>

      {agent.step && (
        <p className="mt-1 truncate font-mono text-[11px] text-status-running" title={agent.step}>
          ▸ {agent.step}
        </p>
      )}

      {/* Measured, then assigned — never the other way round. */}
      <div className="mt-2 space-y-2">
        <div className="border-l-2 border-status-failed/60 pl-2">
          <p className="stub-label">Issue · measured by the simulation</p>
          <p
            className={clsx(
              "text-xs",
              agent.issue ? "text-slate-200" : "italic text-slate-500",
            )}
          >
            {agent.issue ?? "No measured failure assigned to this agent."}
          </p>
        </div>
        <div className="border-l-2 border-surface-border pl-2">
          <p className="stub-label">Task · what we asked for</p>
          <p
            className={clsx("text-xs text-slate-400", dense && "line-clamp-2")}
            title={agent.task}
          >
            {agent.task}
          </p>
        </div>
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px] text-slate-500">
        <div className="min-w-0">
          <dt className="text-slate-600">cluster</dt>
          <dd className="truncate text-slate-400">
            {agent.cluster_id ? (clusterLabel ?? agent.cluster_id) : "—"}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate-600">scenarios</dt>
          <dd className="truncate text-slate-400" title={agent.scenario_ids.join(" ")}>
            {scenarioSummary(agent.scenario_ids)}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate-600">iteration</dt>
          <dd className={clsx("truncate", atCap ? "text-status-blocked" : "text-slate-400")}>
            {agent.iteration}/{agent.max_iterations}
            {atCap ? " · at cap" : ""}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-slate-600">findings</dt>
          <dd className="truncate text-slate-400" title={agent.finding_ids.join(" ")}>
            {agent.finding_ids.length === 0 ? "none yet" : agent.finding_ids.join(" ")}
          </dd>
        </div>
      </dl>

      {agent.last_activity && (
        <p
          className="mt-2 truncate border-l border-surface-border pl-2 font-mono text-[10px] text-slate-400"
          title={agent.last_activity}
        >
          {agent.last_activity}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-3">
        {agent.session_url ? (
          <a
            href={agent.session_url}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-[10px] text-sky-400 hover:underline"
          >
            Devin session ↗
          </a>
        ) : (
          <span className="font-mono text-[10px] text-slate-600">session pending</span>
        )}

        {embeddable ? (
          <>
            <button
              type="button"
              onClick={() => onToggleExpanded(agent.id)}
              className="font-mono text-[10px] text-sky-400 hover:underline"
              aria-expanded={expanded}
            >
              {expanded ? "hide desktop" : "show desktop"}
            </button>
            {onFocus && (
              <button
                type="button"
                onClick={() => onFocus(agent.id)}
                className="font-mono text-[10px] text-slate-400 hover:text-slate-200"
              >
                focus
              </button>
            )}
          </>
        ) : (
          <span className="font-mono text-[10px] text-slate-600">
            {finished ? "machine released" : "no desktop"}
          </span>
        )}
      </div>

      {expanded && embeddable && (
        <div className="mt-2">
          <AgentDesktop
            agent={agent}
            live={paneLive}
            reason={paneReason}
            size="tile"
            onVisibilityChange={onVisibilityChange}
          />
        </div>
      )}
    </div>
  );
}
