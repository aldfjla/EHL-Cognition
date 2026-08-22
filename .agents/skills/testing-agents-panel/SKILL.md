---
name: testing-agents-panel
description: How to exercise the AgentOps panel (apps/ui/components/agents/) end to end in a browser through the canonical run replay, including how to inspect live desktop panes.
---

# Testing the AgentOps panel (apps/ui)

## Surface
- `http://localhost:3000/runs/run_replay_demo` is the no-backend scripted replay.
  Open the Agents tab (press `2`) to exercise `AgentOpsPanel` in the same run
  page where it is mounted.
- The replay takes roughly 60–90 seconds. Start the UI with
  `(cd apps/ui && npm run dev)`. If the server was started by another shell and
  renders unstyled HTML, it is stale — kill it and restart detached
  (`setsid nohup npm run dev > /tmp/uidev.log 2>&1 &`), then confirm HTTP 200.

## Environment
- DISPLAY on these boxes is `:0`. Maximize Chrome before recording with:
  `DISPLAY=:0 wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz`
- Mock embedded desktops point at the static page `/mock/desktop/index.html`, which
  renders a green-on-black fake terminal (`DEVIN DESKTOP · MOCK SESSION`). If a pane is
  blank or shows a browser error, that is a real defect, not a fixture problem.

## The hard part: catching live panes
Agents finish quickly and finished agents cannot embed a desktop. Open the replay
and switch to the Agents tab while the FIX stage is active, before the roster
finishes. The run replay is the source of truth for this panel; do not create a
second route or fixture just to exercise it.

The `MAX_LIVE_PANES` (6) budget may not be exercisable: the replay roster
typically has only 4 agents with a `desktop_url`, so the cap never binds. To test
it, the fixture needs >6 desktop-capable agents held in a working state.

## Things worth asserting
- Cards render `Issue · measured by the simulation` vs `Task · what we asked for`
  as two separately labelled blocks with different left rules.
- "Needs attention" pins blocked + failed agents and is independent of
  `hide N done`; `failed` is not collapsible, so its card must survive the toggle.
- `pause feeds` → button reads `feeds paused`, tiles read `Feeds paused` /
  `Resume feeds to reconnect this view.`, and the counter drops to `0/6 live panes`.
- Finished agents show `machine released` instead of `show desktop`.

## Run-page agent tree (`components/AgentTree.tsx`, tab "2" on a run page)
- `http://localhost:3000/runs/run_replay_demo` (fixture `lib/mockRun.ts`) is the
  polished replay: 10 agents, ends `PR_OPENED` with a `pull request →` header link.
  Press `2` for the Agents tab; wait for the FIX stage before asserting on the
  hierarchy.
- Useful expected values: only `agt_fix_grip_attempt1` is `failed`, so the header
  must read `1 failed` and its parent `Debug Eng #1 — grasp timeout cluster`
  carries a `1✗` subtree badge; the report has incidents for `cl_grip`
  (`scn_03_*`) and `cl_latency` (`scn_17_*`) only, so `agt_inv_trace`
  (`cl_observation`) must show no evidence block.
- Beware a real trap when asserting drawer fields: `lib/useEventStream.ts` (the
  run-page reducer) has **no `agent.updated` case**, so everything the fixture
  sends via `agent.updated` (iteration, issue text, session/desktop urls, step)
  never reaches the run page — the failed agent's drawer shows `iteration 0/3`
  even though the fixture sets `iteration: 3`.
- Likewise no event ever sets an agent's `finished_at`, so `elapsed()` in
  `components/agentTree.ts` treats finished agents as still running and their
  clocks keep ticking. If you are asked to verify that finished agents' clocks
  freeze, expect this to fail until the reducer/fixture sets `finished_at`.
- Filters/collapse/keyboard are pure client state and are safe to test at any
  point: search input is `aria-label="filter agents"`, the scroll container is
  `role="tree" aria-label="agent hierarchy"` (click it to focus, then Arrow keys),
  and the `showing N of M` line only renders when rows < agents.

## Devin Secrets Needed
none — the replay needs no API, database, or credentials.
