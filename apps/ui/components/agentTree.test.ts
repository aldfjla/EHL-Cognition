import { describe, expect, it } from "vitest";

import { elapsed, flatten, indexAgents, matches, parentIds } from "./agentTree";
import type { Agent, AgentStatus, Role } from "@/lib/types";

const BASE: Agent = {
  id: "agt_root",
  run_id: "run_test",
  session_id: null,
  session_url: null,
  role: "investigator",
  title: "Investigator",
  task: "Investigate cluster 1",
  status: "working",
  iteration: 1,
  max_iterations: 3,
  cluster_id: "cl_1",
  scenario_ids: [],
  parent_agent_id: null,
  finding_ids: [],
  last_activity: null,
  desktop_url: null,
  issue: null,
  step: null,
  created_at: "2025-01-01T00:00:00.000Z",
  updated_at: "2025-01-01T00:00:00.000Z",
  finished_at: null,
};

function agent(id: string, over: Partial<Agent> = {}): Agent {
  return { ...BASE, id, ...over };
}

/** parent → child → grandchild, plus an unrelated root. */
function family(): Agent[] {
  return [
    agent("root", { created_at: "2025-01-01T00:00:00.000Z" }),
    agent("child", {
      parent_agent_id: "root",
      role: "fixer" as Role,
      created_at: "2025-01-01T00:00:01.000Z",
    }),
    agent("grandchild", {
      parent_agent_id: "child",
      role: "fixer" as Role,
      status: "failed" as AgentStatus,
      title: "Reproducer",
      created_at: "2025-01-01T00:00:02.000Z",
    }),
    agent("other", {
      role: "reporter" as Role,
      status: "succeeded" as AgentStatus,
      cluster_id: null,
      created_at: "2025-01-01T00:00:03.000Z",
    }),
  ];
}

describe("indexAgents", () => {
  it("nests children under their parent and keeps roots in creation order", () => {
    const { roots, children } = indexAgents(family());
    expect(roots.map((a) => a.id)).toEqual(["root", "other"]);
    expect(children.get("root")?.map((a) => a.id)).toEqual(["child"]);
    expect(children.get("child")?.map((a) => a.id)).toEqual(["grandchild"]);
  });

  it("promotes an agent whose parent is unknown, rather than dropping it", () => {
    const orphan = agent("orphan", { parent_agent_id: "never_seen" });
    const { roots } = indexAgents([orphan]);
    expect(roots.map((a) => a.id)).toEqual(["orphan"]);
  });
});

describe("flatten", () => {
  it("returns depth-first rows with subtree counts", () => {
    const rows = flatten(family());
    expect(rows.map((row) => [row.agent.id, row.depth])).toEqual([
      ["root", 0],
      ["child", 1],
      ["grandchild", 2],
      ["other", 0],
    ]);
    const root = rows[0];
    expect(root?.descendants).toBe(2);
    expect(root?.failing).toBe(1);
    expect(root?.childCount).toBe(1);
  });

  it("hides a collapsed subtree but still reports what it holds", () => {
    const rows = flatten(family(), undefined, new Set(["root"]));
    expect(rows.map((row) => row.agent.id)).toEqual(["root", "other"]);
    expect(rows[0]?.collapsed).toBe(true);
    expect(rows[0]?.descendants).toBe(2);
    expect(rows[0]?.failing).toBe(1);
  });

  it("keeps ancestors of a match so a hit is never orphaned", () => {
    const rows = flatten(family(), {
      query: "reproducer",
      status: "all",
      role: "all",
      clusterId: null,
    });
    expect(rows.map((row) => row.agent.id)).toEqual([
      "root",
      "child",
      "grandchild",
    ]);
  });

  it("filters by status, role and cluster", () => {
    const failed = flatten(family(), {
      query: "",
      status: "failed",
      role: "all",
      clusterId: null,
    });
    expect(failed.map((row) => row.agent.id)).toEqual([
      "root",
      "child",
      "grandchild",
    ]);

    const reporters = flatten(family(), {
      query: "",
      status: "all",
      role: "reporter",
      clusterId: null,
    });
    expect(reporters.map((row) => row.agent.id)).toEqual(["other"]);

    const clustered = flatten(family(), {
      query: "",
      status: "all",
      role: "all",
      clusterId: "cl_1",
    });
    expect(clustered.map((row) => row.agent.id)).not.toContain("other");
  });

  it("terminates on a parent cycle", () => {
    const a = agent("a", { parent_agent_id: "b" });
    const b = agent("b", { parent_agent_id: "a" });
    expect(() => flatten([a, b])).not.toThrow();
  });
});

describe("matches", () => {
  it("searches title, task, step, issue, cluster and id", () => {
    const target = agent("agt_x", { step: "reproducing", title: "" });
    const base = { status: "all", role: "all", clusterId: null } as const;
    expect(matches(target, { ...base, query: "reproduc" })).toBe(true);
    expect(matches(target, { ...base, query: "agt_x" })).toBe(true);
    expect(matches(target, { ...base, query: "nonsense" })).toBe(false);
  });
});

describe("parentIds", () => {
  it("lists only agents that have children", () => {
    expect(parentIds(family()).sort()).toEqual(["child", "root"]);
  });
});

describe("elapsed", () => {
  const now = Date.parse("2025-01-01T01:02:03.000Z");

  it("counts up to now while the agent is alive", () => {
    expect(elapsed(agent("a"), Date.parse("2025-01-01T00:00:42.000Z"))).toBe(
      "42s",
    );
    expect(elapsed(agent("a"), now)).toBe("1h 02m");
  });

  it("freezes at finished_at once the agent is done", () => {
    const done = agent("a", { finished_at: "2025-01-01T00:04:09.000Z" });
    expect(elapsed(done, now)).toBe("4m 09s");
  });
});
