# Robot CI

**Push robot control code. An autonomous engineering team simulates it, finds
what breaks, fixes it, and opens a pull request with video proof.**

Built for the Cognition track at the TUM.ai / EHL hackathon — *"Devin for X:
building the autonomous layer."*

---

## Why

Devin works because software engineering has a closed loop: write code, run
tests, read the failure, fix, re-run. Every step is programmable and the verdict
is machine-checkable.

Robotics has that loop too. It is called **simulation** — deterministic, fast,
free, and already installed in every serious lab.

And yet robotics teams have no CI. Control code gets written, flashed to a
machine, tried once by hand, and shipped. Regressions surface weeks later on
physical hardware, where they are expensive and occasionally dangerous.

The gap was never the simulator. It is that nobody wired an autonomous engineer
into it.

## What it does

A developer pushes to their robot control repo. With no human in the loop:

| Stage | What happens |
|---|---|
| `RESOLVE_MODEL` | Identify the robot; pull its model from MuJoCo Menagerie, or have an agent synthesize one |
| `BUILD_HARNESS` | An agent binds the pushed code — unmodified — into the simulator |
| `DESIGN_SCENARIOS` | A QA-lead agent picks what to randomize: payload, friction, sensor noise, latency |
| `RUN_SUITE` | Run the code across N randomized worlds in parallel |
| `CLUSTER_FAILURES` | Group failures by root cause |
| `INVESTIGATE` | Fan out one debugging agent per cluster — reproduce, explain, evidence |
| `FIX` | Fan out one fixer per confirmed cause, each self-verifying |
| `VERIFY` | Re-run the **full** suite against all patches merged |
| `REPORT` → `PR_OPENED` | Open a pull request only when the full re-run is green, with the incident report, diff, and verified before/after video pairs |
| `REPORT` → `FAILED_UNRESOLVED` | Publish the incident report as a commit comment and failure status when scenarios, regressions, conflicts, or evidence remain unresolved |

A React dashboard shows it live: which agents exist, what each is working on,
and how they communicate.

## The commitment

> **Agents propose. Simulation disposes.**

Nothing an agent claims is accepted without a simulation result behind it.
`packages/simkit` is deterministic, imports nothing from the agent layer, and
runs standalone. An agent that says "fixed" has fixed nothing until the seeds
that were red come back green.

## The interesting constraint

Devin sessions **cannot talk to each other**. So the orchestrator mediates:
every agent writes findings to a shared blackboard, and the orchestrator relays
them into other sessions' prompts. Each relay is a typed message rendered live
as a team chat and a communication graph.

The chat on the dashboard is not a visualization of collaboration — it *is* the
collaboration. See [`docs/AGENT_ROLES.md`](docs/AGENT_ROLES.md).

## Quickstart

Requires Python 3.12+, Node 20+, `git`, and `gh`. No Docker, no `uv`, no system
ffmpeg needed.

```bash
git clone git@github.com:aldfjla/EHL-Cognition.git
cd EHL-Cognition

./scripts/setup.sh          # venv, editable installs, npm install
cp .env.example .env        # fill in DEVIN_API_KEY and GITHUB_TOKEN

make menagerie              # download the robot model library (~few hundred MB)
make smoke                  # prove the Devin API works before anything else
make dev                    # API on :8000, dashboard on :3000
```

Then trigger a run — either push to the watched repo, or:

```bash
curl -X POST localhost:8000/webhooks/manual \
  -d '{"repo":"your-org/robot-arm-control","sha":"<sha>"}'
```

Building the UI without credentials:

```bash
make seed    # replays a scripted run so every component has live-looking data
```

### Make targets

| | |
|---|---|
| `make setup` | venv, editable installs, npm install |
| `make api` / `make ui` / `make dev` | run the API, the dashboard, or both |
| `make menagerie` | download the robot model library |
| `make smoke` | verify Devin API auth with one throwaway session |
| `make seed` | emit a fake run for UI development |
| `make test` / `make lint` / `make fmt` | pytest, ruff |

## Using it on your own repo

Robot CI is an **external** system watching your repo — your code is never
modified to accommodate it. Point a GitHub push webhook at
`POST /webhooks/github`, and optionally drop a
[`robotci.yaml`](robotci.example.yaml) at your repo root:

```yaml
robot:
  menagerie: franka_emika_panda
control:
  entrypoint: src/controller.py:run
  interface: joint_position
task:
  name: pick_and_place
  success:
    - id: object_in_bin
    - id: no_collision
```

Every field is optional except `entrypoint` — anything omitted is inferred by
the agents.

### Which pushes start a run

```yaml
ci:
  branches: [main, "release/*"]   # globs; `*` crosses `/`
  paths:
    include: [src/*, config/*, robotci.yaml]
    exclude: ["*.md", src/vendor/*]
```

| key | default | meaning |
|---|---|---|
| `ci.branches` | `[main]` | a push to any other ref is ignored |
| `ci.paths.include` | source and config extensions, plus `robotci.yaml` | a run needs at least one changed path in here |
| `ci.paths.exclude` | `*.md`, `*.rst`, `docs/*`, `.github/*`, `LICENSE*`, images, video | subtracted from the include set; excludes win. `exclude: []` disables the defaults |

So a README-only push does not burn a run. Omit the section entirely and the
defaults apply; write an empty list and that is a configured answer, not an
omission — `exclude: []` really means "exclude nothing", and it survives being
cached on your repository. Patterns and paths are compared after stripping a
leading `./` and `/` only, so dot-prefixed entries like `.github/*` or `.env`
match what you would expect.

The webhook cannot read this file — your repo is not cloned yet at that point —
so it evaluates the copy Robot CI cached for your repository (what you entered
when connecting it, then whatever the last run read from `robotci.yaml`), and
stage `TRIGGERED` refreshes that cache from the committed file. A change to
`ci:` therefore takes effect from the *next* push. Until a repo's first run, the
values from the connect form (or the defaults) are used.

A push Robot CI deliberately skips is still answered `200` — an error would
make your webhook page look broken — with a stable reason:

| `reason_code` | |
|---|---|
| `started` | a run was created; `matched_paths` lists why |
| `branch_not_watched` | ref is not in `ci.branches` |
| `no_matching_paths` | nothing changed that matches the path filters |
| `changed_paths_unavailable` | the delivery carried no file lists (>20 commits, >3000 files), so path filters were skipped and the run started anyway — CI never silently skips work it could not inspect |
| `already_in_flight` | redelivery of a `(repo, commit)` that already has a run |
| `not_a_push` | some other GitHub event |
| `branch_deleted`, `no_head_commit` | nothing to simulate |
| `repo_not_connected` | unknown repository (single-repo `TARGET_REPO` mode) |

The ignored body echoes the filters that were applied, so GitHub's delivery
page shows what Robot CI thinks it is watching. The same line is logged.

The repair-agent tree is bounded by `MAX_AGENT_TREE_DEPTH=3` (Investigator
owner → Fixer → Reviewer, the whole repair contract) and
`MAX_AGENT_CHILDREN=4` (three configured fix iterations plus one spare seat).
Hitting either cap is always recorded as a non-fatal error event and a cluster
error, never silently skipped. A refused Fixer seat leaves its cluster
unresolved; a refused Reviewer seat is advisory-only and does not discard a
patch whose originally red seeds passed simkit verification.

## Layout

```
apps/api/            FastAPI orchestrator process — webhooks, REST, WebSocket
apps/ui/             Next.js dashboard (mission control)
packages/contracts/  JSON schemas — the source of truth for every shared shape
packages/orchestrator/  Pipeline state machine, agent roles, Devin client, blackboard
packages/simkit/     THE ORACLE — model resolution, scenarios, scoring, video
scripts/             setup, menagerie fetch, mock run, Devin smoke test
docs/                the build spec (start with ARCHITECTURE.md)
```

## Docs

| | |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Pipeline stages, data flow, process layout |
| [`AGENT_ROLES.md`](docs/AGENT_ROLES.md) | The seven roles, and how isolated sessions become a team |
| [`EVENT_PROTOCOL.md`](docs/EVENT_PROTOCOL.md) | Event schemas and the WebSocket contract |
| [`SIMULATION.md`](docs/SIMULATION.md) | Model resolution, scenarios, scoring, video |
| [`DORMANCY.md`](docs/DORMANCY.md) | Measured idle cost and cold-start latency, and how to reproduce them |
| [`DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | The 90-second stage run |
| [`PITCH.md`](docs/PITCH.md) | The judging narrative |

## Status

**Scaffold.** Structure, contracts and documentation are complete; pipeline
logic, simulation and Devin calls are stubbed.

```bash
grep -rn "TODO(build)" --include=*.py --include=*.ts --include=*.tsx \
  --include=*.md --include=*.sh .
```

gives the implementation checklist. The docs above are the spec those TODOs
implement.
