"use client";

/**
 * AGENT OPERATIONS — what every agent is working on, and its machine.
 *
 * The questions this panel answers, in the order it answers them:
 *   1. what is happening right now, and is anything stuck?
 *   2. per agent: which measured failure it owns, what we asked it to do, where
 *      it is in its own work, and what it has written down;
 *   3. can I watch it — one desktop large, or all of them tiled?
 *
 * All state arrives as props: the panel owns view state only (grouping,
 * collapse, desktop mode, the live-pane budget), never run state.
 *
 * Live panes cost real connections, so mounting is the throttle: only mounted,
 * on-screen panes inside the budget hold a frame, and a global pause drops all
 * of them. See `decidePanes` in ./agentOps.
 */

import clsx from "clsx";
import { useCallback, useMemo, useReducer, useState } from "react";

import AgentDesktop from "./AgentDesktop";
import AgentOpsCard from "./AgentOpsCard";
import DesktopWall from "./DesktopWall";
import {
  INITIAL_AGENT_OPS_STATE,
  MAX_LIVE_PANES,
  agentOpsReducer,
  canEmbedDesktop,
  decidePanes,
  groupAgents,
  headline,
  isCollapsible,
  needsAttention,
  orderAgents,
  summarize,
  type DesktopMode,
  type GroupMode,
  type PaneBlock,
} from "./agentOps";
import { ROLE_LABELS, type Agent } from "@/lib/types";

export interface AgentOpsPanelProps {
  runId: string;
  agents: Agent[];
  onFocusAgent?: (agentId: string) => void;
}

const GROUP_MODES: { value: GroupMode; label: string }[] = [
  { value: "role", label: "role" },
  { value: "cluster", label: "cluster" },
];

const DESKTOP_MODES: { value: DesktopMode; label: string }[] = [
  { value: "list", label: "roster" },
  { value: "focus", label: "focus" },
  { value: "wall", label: "wall" },
];

function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label={label}>
      <span className="stub-label">{label}</span>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={clsx(
            "rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase",
            option.value === value
              ? "border-status-running text-status-running"
              : "border-surface-border text-slate-500 hover:text-slate-300",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function AgentOpsPanel({ runId, agents, onFocusAgent }: AgentOpsPanelProps) {
  const [state, dispatch] = useReducer(agentOpsReducer, INITIAL_AGENT_OPS_STATE);
  const [visibleIds, setVisibleIds] = useState<string[]>([]);

  const onVisibilityChange = useCallback((agentId: string, visible: boolean) => {
    setVisibleIds((prev) => {
      const has = prev.includes(agentId);
      if (visible === has) return prev;
      return visible ? [...prev, agentId] : prev.filter((id) => id !== agentId);
    });
  }, []);

  const focusAgent = useCallback(
    (agentId: string | null) => {
      dispatch({ type: "focus_agent", agentId });
      if (agentId !== null) onFocusAgent?.(agentId);
    },
    [onFocusAgent],
  );

  const ordered = useMemo(() => orderAgents(agents), [agents]);
  const summary = useMemo(() => summarize(agents), [agents]);
  const now = useMemo(() => headline(agents), [agents]);

  const collapsedAway = useMemo(() => ordered.filter(isCollapsible), [ordered]);
  /** The roster actually rendered: done agents hide until asked for. */
  const roster = useMemo(
    () => (state.showFinished ? ordered : ordered.filter((a) => !isCollapsible(a))),
    [ordered, state.showFinished],
  );
  const attention = useMemo(() => ordered.filter(needsAttention), [ordered]);

  const groups = useMemo(
    () => groupAgents(roster, state.groupBy),
    [roster, state.groupBy],
  );

  const focused = useMemo(() => {
    const byId = state.focusedAgentId
      ? agents.find((a) => a.id === state.focusedAgentId)
      : undefined;
    return byId ?? roster.find(canEmbedDesktop) ?? roster[0] ?? null;
  }, [agents, roster, state.focusedAgentId]);

  /**
   * Panes mounted in the current mode — the only candidates for a live feed.
   * The wall mounts a tile per agent (non-embeddable ones fall back), focus
   * mounts one, and the roster mounts whatever the user expanded.
   */
  const paneAgents = useMemo(() => {
    if (state.desktopMode === "wall") return roster;
    if (state.desktopMode === "focus") return focused ? [focused] : [];
    return roster.filter((a) => state.expandedAgentIds.includes(a.id));
  }, [state.desktopMode, state.expandedAgentIds, roster, focused]);

  const panes = useMemo(() => {
    const decisions = decidePanes({
      agents: paneAgents,
      visibleIds,
      paused: state.feedsPaused,
      focusedAgentId: focused?.id ?? null,
    });
    return new Map(decisions.map((d) => [d.agentId, d]));
  }, [paneAgents, visibleIds, state.feedsPaused, focused]);

  const paneFor = useCallback(
    (agentId: string): { live: boolean; reason: PaneBlock | null } =>
      panes.get(agentId) ?? { live: false, reason: null },
    [panes],
  );

  const liveCount = useMemo(
    () => Array.from(panes.values()).filter((p) => p.live).length,
    [panes],
  );

  return (
    <section
      data-run-id={runId}
      className="rounded-lg border border-surface-border bg-surface-raised p-4"
    >
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 className="stub-label">Agent operations · {summary.total} agents</h2>
          {/* "What is happening right now" stays at the top, always. */}
          <p className="mt-0.5 truncate text-sm text-slate-200" title={now}>
            {now}
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
          <SegmentedControl
            label="group"
            value={state.groupBy}
            options={GROUP_MODES}
            onChange={(groupBy) => dispatch({ type: "set_group_by", groupBy })}
          />
          <SegmentedControl
            label="view"
            value={state.desktopMode}
            options={DESKTOP_MODES}
            onChange={(mode) => dispatch({ type: "set_desktop_mode", mode })}
          />
          <button
            type="button"
            aria-pressed={state.feedsPaused}
            onClick={() => dispatch({ type: "set_paused", paused: !state.feedsPaused })}
            className={clsx(
              "rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase",
              state.feedsPaused
                ? "border-status-blocked text-status-blocked"
                : "border-surface-border text-slate-400 hover:text-slate-200",
            )}
          >
            {state.feedsPaused ? "feeds paused" : "pause feeds"}
          </button>
        </div>
      </header>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-slate-500">
        <span className="text-status-running">{summary.active} running</span>
        {summary.blocked > 0 && (
          <span className="text-status-blocked">{summary.blocked} blocked</span>
        )}
        {summary.failed > 0 && (
          <span className="text-status-failed">{summary.failed} failed</span>
        )}
        <span>{summary.finished} finished</span>
        {summary.atCap > 0 && <span>{summary.atCap} at iteration cap</span>}
        <span>
          {liveCount}/{MAX_LIVE_PANES} live panes · {summary.embeddable} watchable
        </span>
        {collapsedAway.length > 0 && (
          <button
            type="button"
            onClick={() => dispatch({ type: "toggle_finished" })}
            className="text-slate-400 hover:text-slate-200"
          >
            {state.showFinished ? "hide" : "show"} {collapsedAway.length} done
          </button>
        )}
      </div>

      {/* Trouble is pinned here so it cannot scroll away in a 20-agent run. */}
      {attention.length > 0 && (
        <div className="mt-3 rounded border border-status-blocked/50 bg-slate-900/40 px-3 py-2">
          <p className="stub-label">Needs attention</p>
          <ul className="mt-1 space-y-1">
            {attention.map((agent) => (
              <li key={agent.id} className="flex min-w-0 items-baseline gap-2 text-xs">
                <span
                  className={clsx(
                    "shrink-0 font-mono text-[10px] uppercase",
                    agent.status === "blocked"
                      ? "text-status-blocked"
                      : "text-status-failed",
                  )}
                >
                  {agent.status}
                </span>
                <span className="shrink-0 text-slate-400">
                  {ROLE_LABELS[agent.role]}
                </span>
                <span className="truncate text-slate-300" title={agent.title}>
                  {agent.title || agent.id}
                </span>
                <span
                  className="truncate font-mono text-[10px] text-slate-500"
                  title={agent.step ?? agent.last_activity ?? ""}
                >
                  {agent.step ?? agent.last_activity ?? ""}
                </span>
                <button
                  type="button"
                  onClick={() => focusAgent(agent.id)}
                  className="ml-auto shrink-0 font-mono text-[10px] text-sky-400 hover:underline"
                >
                  focus
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {agents.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">No agents dispatched yet.</p>
      ) : state.desktopMode === "wall" ? (
        <div className="mt-3">
          <DesktopWall
            agents={roster}
            paneFor={paneFor}
            onFocus={focusAgent}
            onVisibilityChange={onVisibilityChange}
          />
        </div>
      ) : state.desktopMode === "focus" && focused ? (
        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-[1fr_320px]">
          <div className="min-w-0 space-y-3">
            {canEmbedDesktop(focused) ? (
              <AgentDesktop
                agent={focused}
                live={paneFor(focused.id).live}
                reason={paneFor(focused.id).reason}
                size="large"
                onVisibilityChange={onVisibilityChange}
              />
            ) : (
              <AgentDesktop
                agent={focused}
                live={false}
                reason="no_desktop"
                size="large"
              />
            )}
            <AgentOpsCard
              agent={focused}
              expanded={false}
              onToggleExpanded={() => undefined}
            />
          </div>
          <ul className="max-h-[560px] space-y-1 overflow-y-auto">
            {roster.map((agent) => (
              <li key={agent.id}>
                <button
                  type="button"
                  onClick={() => focusAgent(agent.id)}
                  aria-current={agent.id === focused.id}
                  className={clsx(
                    "flex w-full min-w-0 items-baseline gap-2 rounded border px-2 py-1.5 text-left",
                    agent.id === focused.id
                      ? "border-status-running bg-surface"
                      : "border-surface-border hover:border-slate-600",
                  )}
                >
                  <span className="shrink-0 stub-label">{ROLE_LABELS[agent.role]}</span>
                  <span className="truncate text-xs text-slate-300">
                    {agent.title || agent.id}
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-500">
                    {canEmbedDesktop(agent) ? "desktop" : "ticker"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="mt-3 space-y-4">
          {groups.map((group) => {
            const collapsed = state.collapsedGroups.includes(group.key);
            return (
              <div key={group.key}>
                <button
                  type="button"
                  onClick={() => dispatch({ type: "toggle_group", key: group.key })}
                  aria-expanded={!collapsed}
                  className="flex w-full items-baseline gap-2 border-b border-surface-border pb-1 text-left"
                >
                  <span className="font-mono text-[10px] text-slate-500">
                    {collapsed ? "▸" : "▾"}
                  </span>
                  <span className="stub-label">{group.label}</span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {group.active} running · {group.agents.length} total
                  </span>
                  {group.attention > 0 && (
                    <span className="font-mono text-[10px] text-status-blocked">
                      {group.attention} need attention
                    </span>
                  )}
                </button>

                {!collapsed && (
                  <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
                    {group.agents.map((agent) => (
                      <div key={agent.id} className="animate-rise">
                        <AgentOpsCard
                          agent={agent}
                          expanded={state.expandedAgentIds.includes(agent.id)}
                          onToggleExpanded={(agentId) =>
                            dispatch({ type: "toggle_expanded", agentId })
                          }
                          onFocus={focusAgent}
                          paneLive={paneFor(agent.id).live}
                          paneReason={paneFor(agent.id).reason}
                          onVisibilityChange={onVisibilityChange}
                          dense
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default AgentOpsPanel;
