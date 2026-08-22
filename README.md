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
| `REPORT` → `PR_OPENED` | Incident report, diff, and before/after video, as a pull request |

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
