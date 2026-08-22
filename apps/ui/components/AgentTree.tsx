"use client";

/**
 * The org chart of the run: every agent nested under the agent that spawned
 * it, with a detail drawer for whichever one is selected.
 *
 * The hierarchy is the story of the fix loop — an investigator fans out from
 * the orchestrator, and the fixers it justified hang underneath it. The tree
 * renders exactly that lineage from `parent_agent_id`; agents without a parent
 * sit at the root as direct reports of the orchestrator.
 */

import clsx from "clsx";
import { useMemo, useState } from "react";

import {
  ROLE_LABELS,
  type Agent,
  type AgentStatus,
  type Message,
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

export interface AgentTreeProps {
  agents: Agent[];
  messages: Message[];
  focusedAgentId?: string | null;
  onFocusAgent?: (agentId: string | null) => void;
}

interface TreeIndex {
  roots: Agent[];
  children: Map<string, Agent[]>;
}

function byCreation(a: Agent, b: Agent): number {
  return Date.parse(a.created_at) - Date.parse(b.created_at);
}

function index(agents: Agent[]): TreeIndex {
  const known = new Set(agents.map((agent) => agent.id));
  const roots: Agent[] = [];
  const children = new Map<string, Agent[]>();
  for (const agent of agents.slice().sort(byCreation)) {
    // A parent we never heard of is treated as no parent: an orphaned subtree
    // must never silently vanish from the chart.
    if (agent.parent_agent_id !== null && known.has(agent.parent_agent_id)) {
      const siblings = children.get(agent.parent_agent_id) ?? [];
      siblings.push(agent);
      children.set(agent.parent_agent_id, siblings);
    } else {
      roots.push(agent);
    }
  }
  return { roots, children };
}

function Node({
  agent,
  depth,
  tree,
  collapsed,
  onToggle,
  selectedId,
  onSelect,
}: {
  agent: Agent;
  depth: number;
  tree: TreeIndex;
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const kids = tree.children.get(agent.id) ?? [];
  const isCollapsed = collapsed.has(agent.id);
  const selected = agent.id === selectedId;

  return (
    <li>
      <div
        className={clsx(
          "flex items-center gap-2 rounded px-2 py-1.5",
          selected ? "bg-surface text-sky-300" : "hover:bg-surface",
        )}
        style={{ paddingLeft: `${8 + depth * 18}px` }}
      >
        {kids.length > 0 ? (
          <button
            type="button"
            aria-label={isCollapsed ? "expand" : "collapse"}
            onClick={() => onToggle(agent.id)}
            className="w-3 shrink-0 font-mono text-[10px] text-slate-500 hover:text-slate-300"
          >
            {isCollapsed ? "▸" : "▾"}
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
          <span
            className={clsx(
              "ml-auto shrink-0 font-mono text-[10px] uppercase",
              STATUS_TEXT[agent.status],
            )}
          >
            {agent.status}
          </span>
          {kids.length > 0 && (
            <span className="shrink-0 font-mono text-[10px] text-slate-600">
              {kids.length} sub
            </span>
          )}
        </button>
      </div>
      {kids.length > 0 && !isCollapsed && (
        <ul>
          {kids.map((kid) => (
            <Node
              key={kid.id}
              agent={kid}
              depth={depth + 1}
              tree={tree}
              collapsed={collapsed}
              onToggle={onToggle}
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function Drawer({
  agent,
  parent,
  messages,
}: {
  agent: Agent;
  parent: Agent | null;
  messages: Message[];
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
        <span className={clsx("ml-auto font-mono text-[10px] uppercase", STATUS_TEXT[agent.status])}>
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
        <div>
          <dt className="text-[10px] uppercase tracking-widest text-slate-600">
            iteration
          </dt>
          <dd className="font-mono text-slate-300">
            {agent.iteration}/{agent.max_iterations}
          </dd>
        </div>
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
  focusedAgentId = null,
  onFocusAgent,
}: AgentTreeProps) {
  const tree = useMemo(() => index(agents), [agents]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [localSelection, setLocalSelection] = useState<string | null>(null);

  const selectedId = focusedAgentId ?? localSelection;
  const selected = agents.find((agent) => agent.id === selectedId) ?? null;
  const parent =
    selected?.parent_agent_id != null
      ? (agents.find((agent) => agent.id === selected.parent_agent_id) ?? null)
      : null;

  const toggle = (id: string): void =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const select = (id: string): void => {
    setLocalSelection(id);
    onFocusAgent?.(id);
  };

  return (
    <div className="rounded-lg border border-surface-border bg-surface-raised p-4">
      <div className="stub-label">
        Chain of command · {agents.length} agent{agents.length === 1 ? "" : "s"}
      </div>

      {agents.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">No agents dispatched yet.</p>
      ) : (
        <div
          className={clsx(
            "mt-3 grid grid-cols-1 gap-4",
            selected !== null && "lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]",
          )}
        >
          <ul className="min-w-0">
            {tree.roots.map((root) => (
              <Node
                key={root.id}
                agent={root}
                depth={0}
                tree={tree}
                collapsed={collapsed}
                onToggle={toggle}
                selectedId={selectedId}
                onSelect={select}
              />
            ))}
          </ul>
          {selected !== null && (
            <Drawer agent={selected} parent={parent} messages={messages} />
          )}
        </div>
      )}
    </div>
  );
}
