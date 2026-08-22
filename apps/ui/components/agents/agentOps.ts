/**
 * The state and ordering rules behind the agent operations panel.
 *
 * Kept free of React and of JSX on purpose: with a dozen agents across roles
 * and clusters, "which agent do I look at first" and "which desktop panes are
 * allowed to be live" are the decisions that make or break the panel, and they
 * are worth testing directly rather than through a rendered tree.
 */

import { ROLE_LABELS, type Agent, type AgentStatus, type Role } from "@/lib/types";

/** How the roster is sliced. Role answers "who", cluster answers "on what". */
export type GroupMode = "role" | "cluster";

/**
 * How desktops are presented.
 *  - `list`  — no desktops; the roster with per-agent detail (the default).
 *  - `focus` — one agent's desktop large, the roster beside it.
 *  - `wall`  — every embeddable desktop tiled.
 */
export type DesktopMode = "list" | "focus" | "wall";

/**
 * Ceiling on simultaneously live desktop panes.
 *
 * Each pane is a live view of a real machine, so an unbounded wall of twenty is
 * both unreadable and expensive. Panes beyond the budget render a still
 * placeholder with an explicit "over the live budget" reason rather than
 * silently showing nothing.
 */
export const MAX_LIVE_PANES = 6;

export const FINISHED_STATUSES: readonly AgentStatus[] = [
  "succeeded",
  "failed",
  "cancelled",
];

/** Statuses that must never scroll out of sight. */
export const ATTENTION_STATUSES: readonly AgentStatus[] = ["blocked", "failed"];

export function isFinished(agent: Agent): boolean {
  return FINISHED_STATUSES.includes(agent.status);
}

export function needsAttention(agent: Agent): boolean {
  return ATTENTION_STATUSES.includes(agent.status);
}

/**
 * True when an agent may be collapsed away as done.
 *
 * Deliberately narrower than `isFinished`: a `failed` agent is finished but
 * still the most interesting thing on the screen, so it is never collapsed.
 */
export function isCollapsible(agent: Agent): boolean {
  return agent.status === "succeeded" || agent.status === "cancelled";
}

/**
 * True when an embedded live view is worth offering.
 *
 * A finished agent's machine is gone, so its `desktop_url` would render an
 * error page — worse than no frame at all. Those fall back to the ticker.
 */
export function canEmbedDesktop(agent: Agent): boolean {
  return agent.desktop_url !== null && agent.desktop_url !== "" && !isFinished(agent);
}

/** Sort weight per status: trouble first, then live work, then the finished. */
const STATUS_RANK: Record<AgentStatus, number> = {
  blocked: 0,
  failed: 1,
  working: 2,
  starting: 3,
  queued: 4,
  succeeded: 5,
  cancelled: 6,
};

/**
 * Attention order: blocked and failed agents first, then whatever is running,
 * then the finished, each band in dispatch order.
 */
export function byAttention(a: Agent, b: Agent): number {
  const rank = STATUS_RANK[a.status] - STATUS_RANK[b.status];
  if (rank !== 0) return rank;
  const created = Date.parse(a.created_at) - Date.parse(b.created_at);
  if (!Number.isNaN(created) && created !== 0) return created;
  return a.id.localeCompare(b.id);
}

export function orderAgents(agents: Agent[]): Agent[] {
  return agents.slice().sort(byAttention);
}

export interface AgentGroup {
  key: string;
  label: string;
  agents: Agent[];
  /** Agents in this group that are blocked or failed. */
  attention: number;
  /** Agents in this group still running. */
  active: number;
}

const UNCLUSTERED_KEY = "__none__";

/**
 * Slice the roster into groups, ordered so that the group holding the most
 * urgent agent comes first. Groups keep their internal attention order.
 */
export function groupAgents(
  agents: Agent[],
  mode: GroupMode,
  clusterLabels: Record<string, string> = {},
): AgentGroup[] {
  const buckets = new Map<string, Agent[]>();

  for (const agent of orderAgents(agents)) {
    const key =
      mode === "role" ? agent.role : (agent.cluster_id ?? UNCLUSTERED_KEY);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(agent);
    else buckets.set(key, [agent]);
  }

  const groups: AgentGroup[] = [];
  for (const [key, members] of buckets) {
    groups.push({
      key,
      label:
        mode === "role"
          ? ROLE_LABELS[key as Role]
          : key === UNCLUSTERED_KEY
            ? "No cluster"
            : (clusterLabels[key] ?? key),
      agents: members,
      attention: members.filter(needsAttention).length,
      active: members.filter((a) => !isFinished(a)).length,
    });
  }

  // The group containing the most urgent agent leads; ties keep insertion order.
  return groups.sort((a, b) => byAttention(a.agents[0], b.agents[0]));
}

export interface AgentOpsSummary {
  total: number;
  active: number;
  finished: number;
  blocked: number;
  failed: number;
  /** Non-finished agents with an embeddable live view. */
  embeddable: number;
  /** Agents at or over their iteration cap. */
  atCap: number;
}

export function summarize(agents: Agent[]): AgentOpsSummary {
  return {
    total: agents.length,
    active: agents.filter((a) => !isFinished(a)).length,
    finished: agents.filter(isFinished).length,
    blocked: agents.filter((a) => a.status === "blocked").length,
    failed: agents.filter((a) => a.status === "failed").length,
    embeddable: agents.filter(canEmbedDesktop).length,
    atCap: agents.filter(
      (a) => a.max_iterations > 0 && a.iteration >= a.max_iterations,
    ).length,
  };
}

/**
 * One line answering "what is happening right now", for the top of the panel.
 *
 * Trouble outranks progress: a blocked agent is the answer even when five
 * others are making progress, because it is the one that needs a human.
 */
export function headline(agents: Agent[]): string {
  if (agents.length === 0) return "No agents dispatched yet.";

  const ordered = orderAgents(agents);
  const lead = ordered[0];
  const detail = lead.step ?? lead.last_activity;
  const where = `${ROLE_LABELS[lead.role]} — ${lead.title || lead.id}`;

  if (lead.status === "blocked") return `Blocked: ${where}${detail ? ` · ${detail}` : ""}`;
  if (lead.status === "failed") return `Failed: ${where}${detail ? ` · ${detail}` : ""}`;
  if (isFinished(lead)) return `All ${agents.length} agents finished.`;
  return `${where}${detail ? ` · ${detail}` : ""}`;
}

export interface AgentOpsState {
  groupBy: GroupMode;
  desktopMode: DesktopMode;
  /** The agent whose desktop is large in focus mode. */
  focusedAgentId: string | null;
  /** Agents with their desktop expanded inline in list mode. */
  expandedAgentIds: string[];
  /** Finished agents are collapsed away until asked for. */
  showFinished: boolean;
  /** Global freeze on every live pane, so the user can stop paying for them. */
  feedsPaused: boolean;
  /** Group keys the user collapsed. */
  collapsedGroups: string[];
}

export const INITIAL_AGENT_OPS_STATE: AgentOpsState = {
  groupBy: "role",
  desktopMode: "list",
  focusedAgentId: null,
  expandedAgentIds: [],
  showFinished: false,
  feedsPaused: false,
  collapsedGroups: [],
};

export type AgentOpsAction =
  | { type: "set_group_by"; groupBy: GroupMode }
  | { type: "set_desktop_mode"; mode: DesktopMode }
  | { type: "focus_agent"; agentId: string | null }
  | { type: "toggle_expanded"; agentId: string }
  | { type: "toggle_group"; key: string }
  | { type: "toggle_finished" }
  | { type: "set_paused"; paused: boolean };

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function agentOpsReducer(
  state: AgentOpsState,
  action: AgentOpsAction,
): AgentOpsState {
  switch (action.type) {
    case "set_group_by":
      return { ...state, groupBy: action.groupBy, collapsedGroups: [] };

    case "set_desktop_mode":
      return { ...state, desktopMode: action.mode };

    case "focus_agent":
      // Focusing an agent is a request to see it, so it implies focus mode;
      // clearing the focus drops back to the roster rather than a blank pane.
      return {
        ...state,
        focusedAgentId: action.agentId,
        desktopMode:
          action.agentId === null
            ? state.desktopMode === "focus"
              ? "list"
              : state.desktopMode
            : "focus",
      };

    case "toggle_expanded":
      return { ...state, expandedAgentIds: toggle(state.expandedAgentIds, action.agentId) };

    case "toggle_group":
      return { ...state, collapsedGroups: toggle(state.collapsedGroups, action.key) };

    case "toggle_finished":
      return { ...state, showFinished: !state.showFinished };

    case "set_paused":
      return { ...state, feedsPaused: action.paused };

    default:
      return state;
  }
}

/** Why a pane is not showing a live feed. `null` means it is live. */
export type PaneBlock = "paused" | "budget" | "not_visible" | "no_desktop";

export interface PaneDecision {
  agentId: string;
  live: boolean;
  reason: PaneBlock | null;
}

export interface PaneBudgetInput {
  /** Agents whose panes are mounted, in the order they are laid out. */
  agents: Agent[];
  /** Ids the browser reports as actually on screen. */
  visibleIds: readonly string[];
  paused: boolean;
  /** The focused agent is live first, whatever the budget. */
  focusedAgentId?: string | null;
  max?: number;
}

/**
 * Decide which mounted panes may hold an open live connection.
 *
 * Only mounted *and* visible panes are eligible, the focused agent wins the
 * first slot, and everything past the budget is told why it is still rather
 * than left looking broken.
 */
export function decidePanes({
  agents,
  visibleIds,
  paused,
  focusedAgentId = null,
  max = MAX_LIVE_PANES,
}: PaneBudgetInput): PaneDecision[] {
  const visible = new Set(visibleIds);
  const eligible = agents.filter(
    (a) => canEmbedDesktop(a) && visible.has(a.id),
  );

  // The focused agent takes the first slot; the rest fill by attention order.
  const priority = eligible
    .slice()
    .sort((a, b) => {
      if (a.id === focusedAgentId) return -1;
      if (b.id === focusedAgentId) return 1;
      return byAttention(a, b);
    })
    .slice(0, Math.max(0, max));
  const granted = new Set(priority.map((a) => a.id));

  return agents.map((agent) => {
    if (!canEmbedDesktop(agent)) {
      return { agentId: agent.id, live: false, reason: "no_desktop" };
    }
    if (paused) return { agentId: agent.id, live: false, reason: "paused" };
    if (!visible.has(agent.id)) {
      return { agentId: agent.id, live: false, reason: "not_visible" };
    }
    if (!granted.has(agent.id)) {
      return { agentId: agent.id, live: false, reason: "budget" };
    }
    return { agentId: agent.id, live: true, reason: null };
  });
}
