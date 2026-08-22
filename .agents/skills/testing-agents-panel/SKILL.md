---
name: testing-agents-panel
description: How to exercise the AgentOps panel (apps/ui/components/agents/) end to end in a browser using the no-backend /agents-demo replay harness, including how to hold agents in a working state long enough to inspect live desktop panes.
---

# Testing the AgentOps panel (apps/ui)

## Surface
- `http://localhost:3000/agents-demo` is a no-backend harness that replays mock agent
  events incrementally. It may be the only route that renders `AgentOpsPanel` — check
  whether the run page has been wired up before assuming otherwise.
- Do **not** use `make seed` for this panel: it writes an already-finished run, which
  proves nothing about incremental build-up or live desktop panes.
- Start the UI with `(cd apps/ui && npm run dev)`. If the server was started by another
  shell and renders unstyled HTML, it is stale — kill it and restart detached
  (`setsid nohup npm run dev > /tmp/uidev.log 2>&1 &`), then confirm HTTP 200.

## Environment
- DISPLAY on these boxes is `:0`. Maximize Chrome before recording with:
  `DISPLAY=:0 wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz`
- Mock embedded desktops point at the static page `/mock/desktop/index.html`, which
  renders a green-on-black fake terminal (`DEVIN DESKTOP · MOCK SESSION`). If a pane is
  blank or shows a browser error, that is a real defect, not a fixture problem.

## The hard part: catching live panes
Agents finish quickly and finished agents cannot embed a desktop, so at `instant` the
whole roster is done and every pane is a fallback. Two temporary, test-only harness
edits in `apps/ui/app/agents-demo/page.tsx` make the live states inspectable — add them,
test, then **revert before finalizing** (verify with `git status --short`):
1. Add a slower speed option, e.g. `{ label: "0.12x", value: 8 }` to `SPEEDS`.
2. In `rosterReducer`'s `agent.status_changed` case, ignore terminal statuses when
   `window.location.search` contains `hold=1`, then browse `/agents-demo?hold=1` to keep
   every agent working indefinitely.
With the hold in place, `wall` view shows several `LIVE` terminal tiles at once, which is
also the easiest way to exercise the pause control and the fallback tiles side by side.

## Things worth asserting
- Cards render `Issue · measured by the simulation` vs `Task · what we asked for` as two
  separately labelled blocks with different left rules.
- "Needs attention" pins blocked + failed agents and is independent of `hide N done`;
  `failed` is not collapsible, so its card must survive the toggle.
- `pause feeds` → button reads `feeds paused`, tiles read `Feeds paused` /
  `Resume feeds to reconnect this view.`, and the counter drops to `0/6 live panes`.
- Finished agents show `machine released` instead of `show desktop`.
- The `MAX_LIVE_PANES` (6) budget may not be exercisable: the mock roster typically has
  only 4 agents with a `desktop_url`, so the cap never binds. To test it, the fixture
  needs >6 desktop-capable agents held in a working state.

## Devin Secrets Needed
none — the harness needs no API, database, or credentials.
