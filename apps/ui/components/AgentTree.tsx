"use client";

/**
 * The org chart of the run: every agent nested under the agent that spawned
 * it, with a detail drawer for whichever one is selected.
 *
 * The hierarchy is the story of the fix loop — an investigator fans out from
 * the orchestrator, and the fixers it justified hang underneath it. The tree
 * renders exactly that lineage from `parent_agent_id`; agents without a parent
 * sit at the root as direct reports of the orchestrator.
 *
 * A full run is fifty scenario agents plus their fix sub-trees, so the list has
 * to stay legible at that size: it filters (text, status, role, cluster),
 * collapses, scrolls inside a fixed frame, and moves under the arrow keys. All
 * of the shaping lives in `agentTree.ts` — this file only draws it.
 */

import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  NO_FILTER,
  elapsed,
  flatten,
  parentIds,
  type StatusFilter,
  type TreeFilter,
  type TreeNode,
} from "@/components/agentTree";
import {
  ROLE_LABELS,
  type Agent,
  type AgentStatus,
  type Incident,
  type Message,
  type Role,
} from "@/lib/types";

const STATUS_DOT: Record<AgentStatus, string> = {
  queued: "bg-status-pending",
  starting: "bg-status-running/60 animate-pulse",
  working: "bg-status-running animate-pulse",
  blocked: "bg-status-blocked",
  succeeded: "bg-status-passed",
  failed: "bg-status-failed",
  cancelled: "bg-status-pending",
};

const STATUS_TEXT: Record<AgentStatus, string> = {
  queued: "text-slate-400",
  starting: "text-status-running",
  working: "text-status-running",
  blocked: "text-status-blocked",
  succeeded: "text-status-passed",
  failed: "text-status-failed",
  cancelled: "text-slate-500",
};

const STATUS_FILTERS: Array<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "all" },
  { id: "active", label: "working" },
  { id: "failed", label: "failed" },
  { id: "finished", label: "done" },
];

export interface AgentTreeProps {
  agents: Agent[];
  messages: Message[];
  /** Report incidents, used to show the evidence a fix sub-tree produced. */
  incidents?: Incident[];
  focusedAgentId?: string | null;
  onFocusAgent?: (agentId: string | null) => void;
}

function Row({
  node,
  selected,
  onToggle,
  onSelect,
  now,
}: {
  node: TreeNode;
  selected: boolean;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  now: number;
}) {
  const { agent, childCount, descendants, failing } = node;

  return (
    <div
      role="treeitem"
      aria-selected={selected}
      aria-expanded={childCount > 0 ? !node.collapsed : undefined}
      className={clsx(
        "flex items-center gap-2 rounded px-2 py-1.5",
        selected ? "bg-surface text-sky-300" : "hover:bg-surface",
      )}
      style={{ paddingLeft: `${8 + node.depth * 18}px` }}
    >
      {childCount > 0 ? (
        <button
          type="button"
          aria-label={node.collapsed ? "expand" : "collapse"}
          onClick={() => onToggle(agent.id)}
          className="w-3 shrink-0 font-mono text-[10px] text-slate-500 hover:text-slate-300"
        >
          {node.collapsed ? "▸" : "▾"}
        </button>
      ) : (
        <span className="w-3 shrink-0 text-center font-mono text-[10px] text-slate-700">
          ·
        </span>
      )}
      <span
        aria-hidden
        className={clsx("h-2 w-2 shrink-0 rounded-full", STATUS_DOT[agent.status])}
      />
      <button
        type="button"
        onClick={() => onSelect(agent.id)}
        className="flex min-w-0 flex-1 items-baseline gap-2 text-left"
      >
        <span className="truncate font-mono text-xs text-slate-200">
          {agent.title || ROLE_LABELS[agent.role]}
        </span>
        <span className="shrink-0 text-[10px] uppercase tracking-widest text-slate-600">
          {ROLE_LABELS[agent.role]}
        </span>
        {agent.step !== null && (
          <span className="hidden min-w-0 shrink truncate font-mono text-[10px] text-slate-500 sm:block">
            {agent.step}
          </span>
        )}
        <span
          className={clsx(
            "ml-auto shrink-0 font-mono text-[10px] uppercase",
            STATUS_TEXT[agent.status],
          )}
        >
          {agent.status}
        </span>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-slate-600">
          {elapsed(agent, now)}
        </span>
        {node.collapsed && descendants > 0 && (
          <span className="shrink-0 font-mono text-[10px] text-slate-600">
            +{descendants}
          </span>
        )}
        {failing > 0 && (
          <span className="shrink-0 font-mono text-[10px] text-status-failed">
            {failing}✗
          </span>
        )}
      </button>
    </div>
  );
}

function Evidence({ incident }: { incident: Incident }) {
  return (
    <div className="mt-4">
      <div className="text-[10px] uppercase tracking-widest text-slate-600">
        evidence
      </div>
      <ul className="mt-1 space-y-1 font-mono text-[10px]">
        <li className="text-slate-400">
          failing run:{" "}
          {incident.before_video !== null ? (
            <span className="text-slate-300">{incident.before_video}</span>
          ) : (
            <span className="text-status-blocked">no recording</span>
          )}
        </li>
        <li className="text-slate-400">
          after the fix:{" "}
          {incident.after_video !== null ? (
            <span className="text-status-passed">{incident.after_video}</span>
          ) : (
            <span className="text-status-blocked">
              not verified on video yet
            </span>
          )}
        </li>
      </ul>
    </div>
  );
}

function Drawer({
  agent,
  parent,
  messages,
  incident,
  now,
}: {
  agent: Agent;
  parent: Agent | null;
  messages: Message[];
  incident: Incident | null;
  now: number;
}) {
  const traffic = messages
    .filter(
      (message) =>
        message.from_agent_id === agent.id || message.to_agent_id === agent.id,
    )
    .slice(-6);

  return (
    <div className="min-w-0 border-l border-surface-border pl-4">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={clsx("h-2 w-2 rounded-full", STATUS_DOT[agent.status])}
        />
        <span className="truncate font-mono text-sm text-slate-100">
          {agent.title || agent.id}
        </span>
        <span
          className={clsx(
            "ml-auto font-mono text-[10px] uppercase",
            STATUS_TEXT[agent.status],
          )}
        >
          {agent.status}
        </span>
      </div>

      <dl className="mt-3 space-y-2 text-xs">
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            role
          </dt>
          <dd className="text-slate-300">{ROLE_LABELS[agent.role]}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            reports to
          </dt>
          <dd className="text-slate-300">
            {parent === null
              ? "orchestrator"
              : parent.title || ROLE_LABELS[parent.role]}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            task
          </dt>
          <dd className="text-slate-300">{agent.task || "—"}</dd>
        </div>
        {agent.issue !== null && (
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-slate-600">
              working on
            </dt>
            <dd className="text-slate-300">{agent.issue}</dd>
          </div>
        )}
        {agent.step !== null && (
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-slate-600">
              current step
            </dt>
            <dd className="font-mono text-slate-300">{agent.step}</dd>
          </div>
        )}
        <div className="flex gap-6">
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-slate-600">
              iteration
            </dt>
            <dd className="font-mono text-slate-300">
              {agent.iteration}/{agent.max_iterations}
            </dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-slate-600">
              elapsed
            </dt>
            <dd className="font-mono tabular-nums text-slate-300">
              {elapsed(agent, now)}
            </dd>
          </div>
          {agent.scenario_ids.length > 0 && (
            <div>
              <dt className="text-[10px] uppercase tracking-widest text-slate-600">
                scenarios
              </dt>
              <dd className="font-mono text-slate-300">
                {agent.scenario_ids.length}
              </dd>
            </div>
          )}
        </div>
        {agent.cluster_id !== null && (
          <div>
            <dt className="text-[10px] uppercase tracking-widest text-slate-600">
              cluster
            </dt>
            <dd className="font-mono text-slate-300">{agent.cluster_id}</dd>
          </div>
        )}
      </dl>

      {agent.session_url !== null && (
        <a
          href={agent.session_url}
          target="_blank"
          rel="noreferrer"
          className="mt-3 block font-mono text-[10px] text-sky-400 hover:underline"
        >
          open Devin session ↗
        </a>
      )}

      {incident !== null && <Evidence incident={incident} />}

      {agent.last_activity !== null && (
        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-widest text-slate-600">
            latest activity
          </div>
          <p className="mt-1 font-mono text-[10px] text-slate-400">
            {agent.last_activity}
          </p>
        </div>
      )}

      {traffic.length > 0 && (
        <div className="mt-4">
          <div className="text-[10px] uppercase tracking-widest text-slate-600">
            recent traffic
          </div>
          <ul className="mt-1 space-y-1">
            {traffic.map((message) => (
              <li
                key={message.id}
                className="truncate border-l border-surface-border pl-2 font-mono text-[10px] text-slate-400"
                title={message.body}
              >
                <span className="text-slate-600">
                  {message.from_agent_id === agent.id ? "→" : "←"} {message.kind}:
                </span>{" "}
                {message.body}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function AgentTree({
  agents,
  messages,
  incidents = [],
  focusedAgentId = null,
  onFocusAgent,
}: AgentTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [localSelection, setLocalSelection] = useState<string | null>(null);
  const [filter, setFilter] = useState<TreeFilter>(NO_FILTER);
  const [now, setNow] = useState<number>(() => Date.now());
  const listRef = useRef<HTMLDivElement>(null);

  // One clock for every row, so fifty timers do not become fifty renders.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const rows = useMemo(
    () => flatten(agents, filter, collapsed),
    [agents, filter, collapsed],
  );
  const roles = useMemo(
    () => [...new Set(agents.map((agent) => agent.role))].sort(),
    [agents],
  );

  const selectedId = focusedAgentId ?? localSelection;
  const selected = agents.find((agent) => agent.id === selectedId) ?? null;
  const parent =
    selected?.parent_agent_id != null
      ? (agents.find((agent) => agent.id === selected.parent_agent_id) ?? null)
      : null;
  const incident =
    selected?.cluster_id != null
      ? (incidents.find((item) => item.cluster_id === selected.cluster_id) ??
        null)
      : null;

  const toggle = useCallback((id: string): void => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const select = useCallback(
    (id: string): void => {
      setLocalSelection(id);
      onFocusAgent?.(id);
    },
    [onFocusAgent],
  );

  /** Arrow keys walk the flattened rows; left/right work the twisty. */
  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (rows.length === 0) return;
    const at = rows.findIndex((row) => row.agent.id === selectedId);
    const step = (delta: number): void => {
      const next = rows[Math.min(rows.length - 1, Math.max(0, at + delta))];
      if (next !== undefined) select(next.agent.id);
    };
    if (event.key === "ArrowDown") {
      event.preventDefault();
      step(at < 0 ? rows.length : 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      step(at < 0 ? 0 : -1);
    } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      const row = rows[at];
      if (row === undefined || row.childCount === 0) return;
      const wantCollapsed = event.key === "ArrowLeft";
      if (row.collapsed !== wantCollapsed) {
        event.preventDefault();
        toggle(row.agent.id);
      }
    }
  };

  const parents = useMemo(() => parentIds(agents), [agents]);
  const allCollapsed = parents.length > 0 && parents.every((id) => collapsed.has(id));
  const failing = agents.filter((agent) => agent.status === "failed").length;

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="stub-label">
          Chain of command · {agents.length} agent
          {agents.length === 1 ? "" : "s"}
          {failing > 0 && (
            <span className="ml-2 text-status-failed">{failing} failed</span>
          )}
        </div>
        {parents.length > 0 && (
          <button
            type="button"
            onClick={() =>
              setCollapsed(allCollapsed ? new Set() : new Set(parents))
            }
            className="font-mono text-[10px] text-slate-500 hover:text-slate-300"
          >
            {allCollapsed ? "expand all" : "collapse all"}
          </button>
        )}
      </div>

      {agents.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No agents dispatched yet.</p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={filter.query}
              onChange={(event) =>
                setFilter((current) => ({
                  ...current,
                  query: event.target.value,
                }))
              }
              placeholder="filter agents…"
              aria-label="filter agents"
              className="min-w-0 flex-1 rounded border border-surface-border bg-surface px-2 py-1 font-mono text-xs text-slate-200 placeholder:text-slate-600 focus:border-sky-600 focus:outline-none"
            />
            <div className="flex items-center gap-1">
              {STATUS_FILTERS.map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={filter.status === id}
                  onClick={() =>
                    setFilter((current) => ({ ...current, status: id }))
                  }
                  className={clsx(
                    "rounded border px-2 py-0.5 font-mono text-[10px]",
                    filter.status === id
                      ? "border-sky-600 text-sky-300"
                      : "border-surface-border text-slate-500 hover:text-slate-300",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <select
              value={filter.role}
              aria-label="filter by role"
              onChange={(event) =>
                setFilter((current) => ({
                  ...current,
                  role: event.target.value as Role | "all",
                }))
              }
              className="rounded border border-surface-border bg-surface px-2 py-1 font-mono text-[10px] text-slate-300 focus:border-sky-600 focus:outline-none"
            >
              <option value="all">every role</option>
              {roles.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </div>

          <div
            className={clsx(
              "mt-3 grid grid-cols-1 gap-4",
              selected !== null && "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]",
            )}
          >
            <div className="min-w-0">
              {rows.length === 0 ? (
                <p className="py-6 text-center text-xs text-slate-500">
                  No agent matches this filter.
                </p>
              ) : (
                <div
                  ref={listRef}
                  role="tree"
                  aria-label="agent hierarchy"
                  tabIndex={0}
                  onKeyDown={onKeyDown}
                  className="max-h-[26rem] overflow-y-auto pr-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-sky-700"
                >
                  {rows.map((row) => (
                    <Row
                      key={row.agent.id}
                      node={row}
                      selected={row.agent.id === selectedId}
                      onToggle={toggle}
                      onSelect={select}
                      now={now}
                    />
                  ))}
                </div>
              )}
              {rows.length < agents.length && (
                <p className="mt-2 font-mono text-[10px] text-slate-600">
                  showing {rows.length} of {agents.length}
                </p>
              )}
            </div>
            {selected !== null && (
              <Drawer
                agent={selected}
                parent={parent}
                messages={messages}
                incident={incident}
                now={now}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
