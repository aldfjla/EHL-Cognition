/**
 * Tree shaping for the chain of command, kept out of the component so the
 * interesting behaviour is testable without a DOM.
 *
 * A run fans out to fifty scenario agents, each of which may spawn its own
 * reproduce/root-cause/fix/verify children. That is far more rows than a flat
 * list can carry, so the view is a filtered, collapsible tree flattened to a
 * single ordered list: one array the component can render, count, and walk with
 * the arrow keys without ever recursing in JSX.
 */

import type { Agent, AgentStatus, Role } from "@/lib/types";

/** Statuses an agent can be in while it still has work left to do. */
const ACTIVE: ReadonlySet<AgentStatus> = new Set<AgentStatus>([
  "queued",
  "starting",
  "working",
  "blocked",
]);

export type StatusFilter = "all" | "active" | "failed" | "finished";

export interface TreeFilter {
  /** Free text over title, role, task, issue, step, cluster and id. */
  query: string;
  status: StatusFilter;
  role: Role | "all";
  clusterId: string | null;
}

export const NO_FILTER: TreeFilter = {
  query: "",
  status: "all",
  role: "all",
  clusterId: null,
};

export interface TreeNode {
  agent: Agent;
  depth: number;
  /** Direct children, before filtering — what the twisty would reveal. */
  childCount: number;
  /** Everything below this agent, at any depth. */
  descendants: number;
  /** Failed agents in the subtree, including this one. Drives the red badge. */
  failing: number;
  /** True when children exist but are hidden, either collapsed or filtered. */
  collapsed: boolean;
}

export interface TreeIndex {
  roots: Agent[];
  children: Map<string, Agent[]>;
}

function byCreation(a: Agent, b: Agent): number {
  return Date.parse(a.created_at) - Date.parse(b.created_at);
}

/**
 * Group agents by parent.
 *
 * An agent whose `parent_agent_id` names someone we have never heard of is
 * promoted to a root: an orphaned subtree must never silently vanish from the
 * chart just because its parent's event has not arrived yet.
 */
export function indexAgents(agents: Agent[]): TreeIndex {
  const known = new Set(agents.map((agent) => agent.id));
  const roots: Agent[] = [];
  const children = new Map<string, Agent[]>();
  for (const agent of agents.slice().sort(byCreation)) {
    const parent = agent.parent_agent_id;
    if (parent !== null && parent !== agent.id && known.has(parent)) {
      const siblings = children.get(parent) ?? [];
      siblings.push(agent);
      children.set(parent, siblings);
    } else {
      roots.push(agent);
    }
  }
  return { roots, children };
}

function haystack(agent: Agent): string {
  return [
    agent.title,
    agent.role,
    agent.task,
    agent.issue ?? "",
    agent.step ?? "",
    agent.cluster_id ?? "",
    agent.id,
  ]
    .join(" ")
    .toLowerCase();
}

/** Does this agent match on its own, ignoring its relatives? */
export function matches(agent: Agent, filter: TreeFilter): boolean {
  if (filter.role !== "all" && agent.role !== filter.role) return false;
  if (filter.clusterId !== null && agent.cluster_id !== filter.clusterId) {
    return false;
  }
  if (filter.status === "active" && !ACTIVE.has(agent.status)) return false;
  if (filter.status === "failed" && agent.status !== "failed") return false;
  if (
    filter.status === "finished" &&
    agent.status !== "succeeded" &&
    agent.status !== "cancelled"
  ) {
    return false;
  }
  const query = filter.query.trim().toLowerCase();
  if (query !== "" && !haystack(agent).includes(query)) return false;
  return true;
}

/**
 * Flatten the tree to the rows that should be on screen.
 *
 * Filtering keeps context: an agent is kept when it matches *or* when anything
 * below it matches, so a search for one failing scenario still shows the parent
 * that spawned it rather than a detached row. Ancestors kept only for context
 * are never auto-hidden by a collapsed state they did not choose — collapsing
 * is explicit, and a collapsed row reports how much it is hiding.
 */
export function flatten(
  agents: Agent[],
  filter: TreeFilter = NO_FILTER,
  collapsed: ReadonlySet<string> = new Set(),
): TreeNode[] {
  const tree = indexAgents(agents);

  const kept = new Map<string, boolean>();
  const keep = (agent: Agent): boolean => {
    const cached = kept.get(agent.id);
    if (cached !== undefined) return cached;
    // Mark before recursing: a cycle in parent links must not hang the view.
    kept.set(agent.id, false);
    const children = tree.children.get(agent.id) ?? [];
    const result =
      matches(agent, filter) || children.some((child) => keep(child));
    kept.set(agent.id, result);
    return result;
  };

  const rows: TreeNode[] = [];
  const walk = (agent: Agent, depth: number): void => {
    if (!keep(agent)) return;
    const children = tree.children.get(agent.id) ?? [];
    const visible = children.filter((child) => keep(child));
    const hidden = collapsed.has(agent.id);
    const node: TreeNode = {
      agent,
      depth,
      childCount: visible.length,
      descendants: 0,
      failing: agent.status === "failed" ? 1 : 0,
      collapsed: hidden && visible.length > 0,
    };
    rows.push(node);
    if (hidden) {
      // Still count what is behind the twisty, so a collapsed parent can say
      // "12 below, 3 failing" instead of hiding the only bad news.
      const stats = subtreeStats(tree, agent, keep);
      node.descendants = stats.descendants;
      node.failing += stats.failing;
      return;
    }
    const before = rows.length;
    for (const child of visible) walk(child, depth + 1);
    for (const row of rows.slice(before)) {
      node.descendants += 1;
      node.failing += row.agent.status === "failed" ? 1 : 0;
    }
  };

  for (const root of tree.roots) walk(root, 0);
  return rows;
}

function subtreeStats(
  tree: TreeIndex,
  agent: Agent,
  keep: (agent: Agent) => boolean,
): { descendants: number; failing: number } {
  let descendants = 0;
  let failing = 0;
  const seen = new Set<string>([agent.id]);
  const stack = [...(tree.children.get(agent.id) ?? [])];
  while (stack.length > 0) {
    const next = stack.pop();
    if (next === undefined || seen.has(next.id) || !keep(next)) continue;
    seen.add(next.id);
    descendants += 1;
    failing += next.status === "failed" ? 1 : 0;
    stack.push(...(tree.children.get(next.id) ?? []));
  }
  return { descendants, failing };
}

/** Every agent that has at least one child, for expand/collapse all. */
export function parentIds(agents: Agent[]): string[] {
  return [...indexAgents(agents).children.keys()];
}

/**
 * Wall-clock time the agent has been alive, as a compact `1h 02m` / `4m 09s`
 * string. `now` is passed in so the caller owns the clock and the output is
 * deterministic under test.
 */
export function elapsed(agent: Agent, now: number): string {
  const start = Date.parse(agent.created_at);
  const end =
    agent.finished_at !== null ? Date.parse(agent.finished_at) : now;
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  const pad = (value: number): string => String(value).padStart(2, "0");
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${pad(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${pad(minutes % 60)}m`;
}
