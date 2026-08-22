import { describe, expect, it } from "vitest";

import {
  INITIAL_AGENT_OPS_STATE,
  agentOpsReducer,
  byAttention,
  canEmbedDesktop,
  decidePanes,
  groupAgents,
  headline,
  orderAgents,
  summarize,
} from "./agentOps";
import type { Agent } from "@/lib/types";

const BASE_AGENT: Agent = {
  id: "agent",
  run_id: "run_test",
  session_id: "session",
  session_url: "https://app.devin.ai/sessions/session",
  role: "investigator",
  title: "Investigator",
  task: "Investigate the failure",
  status: "working",
  iteration: 1,
  max_iterations: 3,
  cluster_id: "cluster",
  scenario_ids: ["scenario"],
  parent_agent_id: null,
  finding_ids: [],
  last_activity: "working",
  desktop_url: "https://desktop.test/agent",
  issue: null,
  step: "reproducing",
  created_at: "2025-01-01T00:00:00.000Z",
  updated_at: "2025-01-01T00:00:00.000Z",
  finished_at: null,
};

function agent(overrides: Partial<Agent> = {}): Agent {
  return { ...BASE_AGENT, ...overrides };
}

describe("agent ordering", () => {
  it("puts attention first, active work next, and finished agents last", () => {
    const agents = [
      agent({ id: "finished", status: "succeeded" }),
      agent({ id: "queued", status: "queued" }),
      agent({ id: "blocked", status: "blocked" }),
      agent({ id: "failed", status: "failed" }),
      agent({ id: "starting", status: "starting" }),
      agent({ id: "working", status: "working" }),
    ];

    expect(orderAgents(agents).map((item) => item.id)).toEqual([
      "blocked",
      "failed",
      "working",
      "starting",
      "queued",
      "finished",
    ]);
  });

  it("breaks status ties by creation time and then id", () => {
    const sameTime = "2025-01-01T00:00:00.000Z";
    const later = agent({
      id: "later",
      status: "working",
      created_at: "2025-01-02T00:00:00.000Z",
    });
    const earlier = agent({
      id: "earlier",
      status: "working",
      created_at: sameTime,
    });
    const sameTimeHigherId = agent({
      id: "zulu",
      status: "working",
      created_at: sameTime,
    });
    const sameTimeLowerId = agent({
      id: "alpha",
      status: "working",
      created_at: sameTime,
    });

    expect([later, sameTimeHigherId, sameTimeLowerId, earlier].sort(byAttention).map((item) => item.id)).toEqual([
      "alpha",
      "earlier",
      "zulu",
      "later",
    ]);
  });
});

describe("groupAgents", () => {
  it("groups by role with counts and urgent-group ordering", () => {
    const groups = groupAgents(
      [
        agent({ id: "qa", role: "scenario_designer", status: "working" }),
        agent({ id: "fix", role: "fixer", status: "failed" }),
        agent({ id: "review", role: "reviewer", status: "succeeded" }),
        agent({ id: "fix-working", role: "fixer", status: "working" }),
      ],
      "role",
    );

    expect(groups.map((group) => group.label)).toEqual([
      "Fix Engineer",
      "QA Lead",
      "Tech Lead",
    ]);
    expect(groups[0]).toMatchObject({
      key: "fixer",
      attention: 1,
      active: 1,
    });
    expect(groups[0].agents.map((item) => item.id)).toEqual(["fix", "fix-working"]);
  });

  it("groups by cluster, labels null and unknown clusters, and counts activity", () => {
    const groups = groupAgents(
      [
        agent({ id: "none", cluster_id: null, status: "working" }),
        agent({ id: "raw", cluster_id: "cl_raw", status: "succeeded" }),
        agent({ id: "known", cluster_id: "cl_known", status: "blocked" }),
      ],
      "cluster",
      { cl_known: "Known diagnosis" },
    );

    expect(groups.map((group) => [group.key, group.label])).toEqual([
      ["cl_known", "Known diagnosis"],
      ["__none__", "No cluster"],
      ["cl_raw", "cl_raw"],
    ]);
    expect(groups[0]).toMatchObject({ attention: 1, active: 1 });
    expect(groups[1]).toMatchObject({ attention: 0, active: 1 });
    expect(groups[2]).toMatchObject({ attention: 0, active: 0 });
  });
});

describe("summarize and headline", () => {
  it("describes an empty roster", () => {
    expect(summarize([])).toEqual({
      total: 0,
      active: 0,
      finished: 0,
      blocked: 0,
      failed: 0,
      embeddable: 0,
      atCap: 0,
    });
    expect(headline([])).toBe("No agents dispatched yet.");
  });

  it("counts active, finished, attention, embeddable, and capped agents", () => {
    expect(summarize([
      agent({ id: "blocked", status: "blocked" }),
      agent({ id: "failed", status: "failed", iteration: 3 }),
      agent({ id: "working", status: "working" }),
      agent({ id: "finished", status: "succeeded" }),
    ])).toEqual({
      total: 4,
      active: 2,
      finished: 2,
      blocked: 1,
      failed: 1,
      embeddable: 2,
      atCap: 1,
    });
  });

  it("gives a blocked agent priority over working agents", () => {
    const blocked = agent({
      id: "blocked",
      status: "blocked",
      step: "waiting for approval",
    });
    const working = agent({ id: "working", status: "working" });

    expect(headline([working, blocked])).toContain("Blocked: Debugging Engineer — Investigator");
    expect(headline([working, blocked])).toContain("waiting for approval");
  });

  it("reports an all-finished roster", () => {
    expect(headline([
      agent({ id: "one", status: "succeeded" }),
      agent({ id: "two", status: "failed" }),
    ])).toBe("Failed: Debugging Engineer — Investigator · reproducing");
    expect(headline([
      agent({ id: "one", status: "succeeded" }),
      agent({ id: "two", status: "cancelled" }),
    ])).toBe("All 2 agents finished.");
  });
});

describe("desktop pane decisions", () => {
  it("only embeds live desktops for unfinished agents with a URL", () => {
    expect(canEmbedDesktop(agent({ status: "succeeded" }))).toBe(false);
    expect(canEmbedDesktop(agent({ desktop_url: null }))).toBe(false);
    expect(canEmbedDesktop(agent({ desktop_url: "" }))).toBe(false);
    expect(canEmbedDesktop(agent({ status: "working", desktop_url: "https://desktop.test/live" }))).toBe(true);
  });

  it("explains no desktop, invisibility, paused feeds, and budget exhaustion", () => {
    const agents = [
      agent({ id: "no-desktop", desktop_url: null }),
      agent({ id: "not-visible", desktop_url: "https://desktop.test/not-visible" }),
      agent({ id: "budget", desktop_url: "https://desktop.test/budget" }),
      agent({ id: "live", desktop_url: "https://desktop.test/live" }),
    ];

    expect(decidePanes({
      agents,
      visibleIds: ["budget", "live"],
      paused: false,
      max: 1,
    })).toEqual([
      { agentId: "no-desktop", live: false, reason: "no_desktop" },
      { agentId: "not-visible", live: false, reason: "not_visible" },
      { agentId: "budget", live: true, reason: null },
      { agentId: "live", live: false, reason: "budget" },
    ]);
    expect(decidePanes({
      agents,
      visibleIds: ["not-visible", "budget", "live"],
      paused: true,
      max: 6,
    }).map((decision) => decision.reason)).toEqual([
      "no_desktop",
      "paused",
      "paused",
      "paused",
    ]);
    expect(decidePanes({
      agents,
      visibleIds: ["budget", "live"],
      paused: false,
      max: 6,
    })[1]).toEqual({ agentId: "not-visible", live: false, reason: "not_visible" });
  });

  it("keeps the focused agent live first without exceeding the budget", () => {
    const agents = [
      agent({ id: "first", created_at: "2025-01-01T00:00:00.000Z" }),
      agent({ id: "focused", created_at: "2025-01-02T00:00:00.000Z" }),
      agent({ id: "third", created_at: "2025-01-03T00:00:00.000Z" }),
    ];
    const decisions = decidePanes({
      agents,
      visibleIds: ["first", "focused", "third"],
      paused: false,
      focusedAgentId: "focused",
      max: 1,
    });

    expect(decisions).toEqual([
      { agentId: "first", live: false, reason: "budget" },
      { agentId: "focused", live: true, reason: null },
      { agentId: "third", live: false, reason: "budget" },
    ]);
    expect(decisions.filter((decision) => decision.live)).toHaveLength(1);
  });
});

describe("agentOpsReducer", () => {
  it("handles every action and preserves the focus-mode subtleties", () => {
    const grouped = agentOpsReducer(
      { ...INITIAL_AGENT_OPS_STATE, collapsedGroups: ["investigator"] },
      { type: "set_group_by", groupBy: "cluster" },
    );
    expect(grouped).toMatchObject({ groupBy: "cluster", collapsedGroups: [] });

    const wall = agentOpsReducer(grouped, { type: "set_desktop_mode", mode: "wall" });
    expect(wall.desktopMode).toBe("wall");
    const focused = agentOpsReducer(wall, { type: "focus_agent", agentId: "agent-1" });
    expect(focused).toMatchObject({ focusedAgentId: "agent-1", desktopMode: "focus" });
    const cleared = agentOpsReducer(focused, { type: "focus_agent", agentId: null });
    expect(cleared).toMatchObject({ focusedAgentId: null, desktopMode: "list" });
    const wallCleared = agentOpsReducer(wall, { type: "focus_agent", agentId: null });
    expect(wallCleared).toMatchObject({ focusedAgentId: null, desktopMode: "wall" });

    const expanded = agentOpsReducer(cleared, { type: "toggle_expanded", agentId: "agent-1" });
    expect(expanded.expandedAgentIds).toEqual(["agent-1"]);
    const collapsed = agentOpsReducer(expanded, { type: "toggle_expanded", agentId: "agent-1" });
    expect(collapsed.expandedAgentIds).toEqual([]);
    const groupCollapsed = agentOpsReducer(collapsed, { type: "toggle_group", key: "cluster" });
    expect(groupCollapsed.collapsedGroups).toEqual(["cluster"]);
    expect(agentOpsReducer(groupCollapsed, { type: "toggle_group", key: "cluster" }).collapsedGroups).toEqual([]);
    expect(agentOpsReducer(groupCollapsed, { type: "toggle_finished" }).showFinished).toBe(true);
    expect(agentOpsReducer(groupCollapsed, { type: "set_paused", paused: true }).feedsPaused).toBe(true);
  });
});
