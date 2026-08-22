# Architecture

## The problem this shape solves

Software engineering has a loop: write code, run tests, read the failure, fix,
re-run. Agents can be dropped into that loop because every step is programmable
and the verdict is machine-checkable.

Robotics has no such loop in practice. Control code is written, flashed onto a
machine, tried once by hand, and shipped. Regressions surface weeks later on
physical hardware, where they cost money and sometimes safety. It is not that
robotics *lacks* a way to check work — simulation has existed for decades — it
is that nobody wired an autonomous engineer into it.

That is the whole system: **take the loop software already has, point it at a
simulator, and put agents inside it.**

## The one architectural commitment

> **Agents propose. Simulation disposes.**

No claim from an agent is accepted without a simulation result behind it. An
agent that says "fixed" has not fixed anything until the suite re-runs green
against the same seeds that were red. This single rule is what separates this
from a system that generates plausible-looking robot code.

It has a direct structural consequence: the codebase splits in two.

| | `packages/simkit` — the oracle | `packages/orchestrator` — the agents |
|---|---|---|
| Determinism | Total. `(model, harness, seed)` → identical result | None. LLM sessions |
| LLM calls | Zero, ever | All of them |
| Trust | Trusted | Verified by the left column |
| Failure means | Our bug | A hypothesis to test |

`simkit` must never import `orchestrator`. The dependency runs one way, so the
oracle can be run, inspected and trusted on its own.

## Process layout

No Docker on the target machine, by constraint. Everything is a plain local
process:

```
┌─────────────────┐        push webhook         ┌──────────────────┐
│  Customer repo  │ ──────────────────────────▶ │  FastAPI :8000   │
│  (robot code)   │ ◀────── branch + PR ─────── │  apps/api        │
└─────────────────┘                             └────────┬─────────┘
                                                         │ in-process
                                        ┌────────────────┴────────────────┐
                                        │  orchestrator.Pipeline (async)  │
                                        └───┬────────────────────────┬────┘
                                            │                        │
                                  HTTPS     │                        │  fork/exec
                                            ▼                        ▼
                                  ┌──────────────────┐   ┌────────────────────┐
                                  │  Devin API       │   │ simkit workers     │
                                  │  (N sessions)    │   │ (ProcessPool)      │
                                  └──────────────────┘   └────────────────────┘
                                            │                        │
                                            └────────┬───────────────┘
                                                     ▼ events
                                            ┌──────────────────┐
                                            │  EventBus        │
                                            └────────┬─────────┘
                                            WS       │
                                                     ▼
                                            ┌──────────────────┐
                                            │  Next.js :3000   │
                                            └──────────────────┘
```

Four things run *during a run*: uvicorn, `npm run dev`, a pool of MuJoCo worker
processes, and N Devin sessions in the cloud. Between runs only the first two
exist — the worker pool is constructed per run and the sessions are created and
released by the pipeline. Measured idle cost and cold-start latency are in
[`DORMANCY.md`](DORMANCY.md). State lives in SQLite (WAL mode, so the pipeline
writes while the dashboard reads) and files under `ARTIFACTS_DIR`.

The pipeline runs **in-process** with the API rather than as a worker queue.
For a system where one run is in flight at a time this removes a broker, a
serialization format and a class of "the dashboard is subscribed to a different
bus than the pipeline publishes to" bugs. The seam to extract it later is
`EventBus` — swap the in-memory implementation for Redis pub/sub and the
pipeline can move to its own process without touching a router.

## Pipeline stages

```
TRIGGERED
   ↓  clone repo @ sha, read robotci.yaml                    [no agent]
RESOLVE_MODEL
   ↓  shipped/converted model or Menagerie lookup; Modeler only on a miss [oracle-first]
BUILD_HARNESS
   ↓  Harness Builder binds pushed code to MuJoCo            [agent + smoke test]
DESIGN_SCENARIOS
   ↓  QA Lead picks ranges; simkit samples from a seed       [agent + oracle]
RUN_SUITE
   ↓  N scenarios in parallel → score table                  [oracle only]
   ├── all green ────────────────────────────────────────▶  PASSED_CLEAN
CLUSTER_FAILURES
   ↓  group failures by signature → K clusters               [no agent]
INVESTIGATE  ⋯ fan-out, one Investigator per cluster         [agents]
   ↓  root causes written to the blackboard
FIX          ⋯ fan-out, one Fixer per confirmed cause        [agents + oracle]
   ↓  patches, each self-verified against its own seeds
VERIFY
   ↓  merge all patches, re-run the FULL suite               [oracle + Tech Lead]
   ├── still failing, budget left ───────────────────────▶  back to FIX
   ├── red/regressed/conflicted ─────────────────────────▶  REPORT
   ├── infrastructure crash ────────────────────────────▶  FAILED_UNRESOLVED
REPORT
   ├── full suite green, no regressions/conflicts ─────────▶ PR_OPENED [terminal]
   └── unresolved findings ────────────────────────────────▶ FAILED_UNRESOLVED [terminal]
```

Terminal states: `PASSED_CLEAN`, `PR_OPENED`, `FAILED_UNRESOLVED`.

Legal transitions are declared in `pipeline.TRANSITIONS` and enforced on every
move, so a bug in a role cannot skip the verification gate.

### Why VERIFY is separate from FIX

Each Fixer verifies its own cluster in isolation, which is fast and cheap. But
independent agents patching one repo will produce patches that pass separately
and fail together. VERIFY is the only stage that sees the merged state, and the
merged state is the only state that ships. Skipping it would make the system
confidently wrong — the worst failure mode available to it.

### Why clustering is not an LLM call

Clustering decides fan-out width, and fan-out width is the dominant cost of a
run. 20 failures from one bug should cost one Investigator, not twenty. The
grouping signal — which criteria failed, the normalised diagnosis text, which
randomized parameters correlate with the failures — is already structured data
produced by `simkit`. Spending an agent to group structured data would be both
slower and less reliable than `clustering.py`.

## Data flow

```
robotci.yaml ─┐
              ├─▶ simkit.scenarios.generate(seed, ranges) ─▶ Scenario[]
QA Lead agent ┘                                                  │
                                                                 ▼
                                              simkit.suite.run_suite (parallel)
                                                                 │
                                            EpisodeResult + diagnosis (measured)
                                                                 │
                                        ┌────────────────────────┴───────────┐
                                        ▼                                    ▼
                             clustering.cluster_failures            recorder → mp4
                                        │
                                     Cluster[]
                                        │
                      ┌─────────────────┼─────────────────┐   (fan-out)
                      ▼                 ▼                 ▼
                Investigator      Investigator      Investigator
                      │                 │                 │
                      └────────▶  blackboard  ◀───────────┘
                                        │
                        orchestrator relays findings between sessions
                                        │
                                     Fixer × K ──▶ workspace worktrees
                                        │
                                     VERIFY (full suite, merged)
                                        │
                                     Reporter ──▶ Report ──▶ PR (green only)
```

The `diagnosis` string is the hinge of the entire system. It is where the
deterministic half hands off to the agent half: `simkit.scoring` measures what
went wrong and writes one English sentence about it, and that sentence is the
Investigator's entire starting position. This is why `scoring.py` is worth more
care than its line count suggests — see `SIMULATION.md`.

## Contracts

`packages/contracts/schemas/*.json` is the source of truth for every shape that
crosses a process boundary. Three mirrors are hand-maintained against it:
`orchestrator/schemas.py` (wire format), `apps/api/app/store/tables.py`
(storage), `apps/ui/lib/types.ts` (client). See `packages/contracts/README.md`
for the sync rules and the codegen escape hatch.

## What is deliberately not here

* **No message broker.** One run at a time; an in-process bus is honest.
* **No container isolation.** The customer's control code runs in a worker
  process with a wall-clock watchdog. That is weaker than a sandbox and is
  stated plainly rather than papered over — this is a CI system you point at
  your own repo, not a multi-tenant service.
* **No agent-to-agent channel.** Devin sessions cannot address each other. The
  orchestrator mediates every exchange. See `AGENT_ROLES.md` — this constraint
  turned out to be the most interesting part of the design.
* **No retry-until-green.** The iteration budget is finite and
  `FAILED_UNRESOLVED` is a legitimate outcome. A red or regressed verification
  writes its report as a commit comment and failure status; it never pushes a
  branch or opens a PR.
