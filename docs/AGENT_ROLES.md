# The engineering team

Seven roles. Each is a Devin session with a prompt contract, a defined slice of
the shared blackboard, and a structured output the pipeline can verify.

## The constraint that shaped everything

**Devin sessions cannot talk to each other.** There is no channel, no shared
memory, no way for one session to address another. Each is an isolated agent
with its own context.

That is not a limitation to route around — it is the interesting problem. A
real engineering team is also made of people who cannot read each other's
minds; what makes them a team is a shared artifact and someone routing
information between them. So:

> **The orchestrator mediates.** Every agent writes findings to a shared
> blackboard. The orchestrator reads the board and splices relevant findings
> into other sessions' prompts. Each relay is emitted as a typed message on the
> bus, which is what the dashboard renders as a chat and a graph.

This is the honest architecture *and* the best part of the demo. The audience
is not being shown a metaphor — the "team chat" on screen is the literal
mechanism by which one agent's conclusion reaches another agent's context. When
a Debugging Engineer's root cause appears in a Fix Engineer's prompt, that
message is the relay, not a visualization of one.

### Why a blackboard rather than direct relay

Agents do not overlap in time. An Investigator often finishes before its Fixer
starts, and a Fixer dispatched in iteration 2 needs constraints established by
a Harness Builder that exited twenty minutes earlier. A durable board handles
both cases; a point-to-point channel would require both parties alive at once.

It also gives the report an audit trail: what the team believed, when, who said
it, and whether it survived verification.

## Message shape

```jsonc
{
  "id": "msg_...",
  "run_id": "run_...",
  "from_role": "investigator",       // or "orchestrator"
  "to_role": "fixer",                // or "broadcast"
  "kind": "handoff",                 // see below
  "body": "markdown — spliced into the recipient's prompt",
  "refs": [{"type": "scenario", "id": "scn_...", "label": "seed 4471"}],
  "ts": "2026-08-22T14:03:11Z"
}
```

`kind` is the speech act, and it drives both the UI treatment and the relay
policy:

| kind | meaning | typical direction |
|---|---|---|
| `hypothesis` | an untested theory | investigator → broadcast |
| `finding` | something established | any → broadcast |
| `question` | a blocked agent needs input | any → orchestrator |
| `answer` | resolution of a question | orchestrator → asker |
| `verdict` | an accept/reject decision | reviewer → fixer |
| `handoff` | ownership transfer with context | investigator → fixer |

## The roles

### 1. Modeler — *Hardware Engineer*
**Stage:** `RESOLVE_MODEL`  ·  **Fan-out:** 0 or 1  ·  **Prompt:** `modeler.md`

Runs **only if** the automatic Menagerie lookup misses. Identifies the robot
from drivers, URDFs and calibration constants, then either names the Menagerie
model the search missed (best outcome) or synthesizes MJCF from the kinematics.

- **Reads:** nothing — it is first.
- **Writes:** `observation` (how the robot was identified), `constraint` (every
  physical quantity it had to guess).
- **Verified by:** the model must load in MuJoCo and hold a pose against
  gravity before the pipeline accepts it.

Its `constraint` findings matter downstream: an Investigator debugging a failure
against a *generated* model needs to know the model itself might be the cause.

### 2. Harness Builder — *Test Infrastructure*
**Stage:** `BUILD_HARNESS`  ·  **Fan-out:** 1  ·  **Prompt:** `harness_builder.md`

Writes the adapter that lets the customer's **unmodified** entrypoint drive
MuJoCo actuators instead of a hardware driver. Highest-leverage role in the
system: everything downstream tests whatever this agent built.

- **Reads:** the Modeler's constraints.
- **Writes:** `constraint` (assumptions the customer's code makes — start pose,
  units, control rate), `observation` (which driver calls were faked).
- **Verified by:** one smoke scenario, checked for actual joint movement.

The dangerous failure here is a harness that runs cleanly and leaves the arm
limp: every subsequent agent then investigates a robot bug that does not exist.
Hence the movement check rather than an exit-code check.

### 3. Scenario Designer — *QA Lead*
**Stage:** `DESIGN_SCENARIOS`  ·  **Fan-out:** 1  ·  **Prompt:** `scenario_designer.md`

Chooses which axes to randomize and over what ranges — not the samples
themselves, which `simkit.scenarios` derives deterministically from a seed so
every failure is reproducible.

- **Reads:** the harness constraints.
- **Writes:** `observation` (hardcoded boundaries found in the controller, with
  `file:line`).

Those boundary observations are the highest-value thing this role produces: a
`GRIP_TIMEOUT = 2.0` at `controller.py:88` tells the QA Lead where to straddle
the range, and tells the Investigator where to look an hour later.

### 4. Investigator — *Debugging Engineer*
**Stage:** `INVESTIGATE`  ·  **Fan-out:** one per cluster  ·  **Prompt:** `investigator.md`

Owns exactly one failure cluster. Must reproduce the failure from its seed
before theorising, and must test its theory by moving one variable and
predicting the result.

- **Reads:** its own cluster's diagnoses and parameter correlation, the team's
  constraints, and other Investigators' `observation` findings — but **not**
  their cluster detail. Narrow scope is deliberate: prompt budget spent on
  another cluster's specifics is budget not spent on this one.
- **Writes:** `root_cause`, `observation`.
- **Explicitly may not patch.** The explanation and the change are separate
  seats so they can be judged independently.

### 5. Fixer
**Stage:** `FIX`  ·  **Fan-out:** one per confirmed cause  ·  **Prompt:** `fixer.md`

Patches one root cause in its own `git worktree`, then self-verifies against its
cluster's seeds and samples previously-passing seeds for regressions.

- **Reads:** the handoff from its Investigator, all active `constraint`
  findings, and the Reviewer's notes on a second iteration.
- **Writes:** `patch`, `verification`.
- **Bounded by** `MAX_AGENT_ITERATIONS`. An exhausted Fixer is failed out and
  its incident is reported unresolved — an honest unresolved beats an unbounded
  spend.

Separate worktrees are not fastidiousness: two agents editing one checkout
corrupt each other's diffs, and the resulting mess presents as a robot bug
rather than an orchestration bug.

### 6. Reviewer — *Tech Lead*
**Stage:** `VERIFY`  ·  **Fan-out:** 1  ·  **Prompt:** `reviewer.md`

The gate. The only role that sees every patch merged together.

- **Reads:** the entire blackboard, the merged diff, before/after suite stats.
- **Writes:** `verification`; promotes `root_cause` findings to `confirmed` or
  `refuted`; marks duplicates `superseded`.
- **Verdicts:** `ship` → REPORT, `iterate` → back to FIX with notes,
  `give_up` → `FAILED_UNRESOLVED`.

Its most important job is rejecting fixes that pass the suite while being
obviously wrong — hardcoding to test seeds, weakening criteria, sim-only special
cases. The suite is a filter, not a certificate.

### 7. Reporter — *Engineering Manager*
**Stage:** `REPORT`  ·  **Fan-out:** 1  ·  **Prompt:** `reporter.md`

Writes the incident report, which is used verbatim as the PR body.

- **Reads:** confirmed findings, suite stats, the diff, the video pairs.
- **Writes:** nothing to the board — it is the end of the chain.

Instructed to lead with findings rather than process and to state plainly what
is still broken. No agent theatre: the developer wants an engineering report,
not a count of sessions.

## Relay policy

What each role is shown from the board. Enforced in `blackboard.for_role()`.

| Role | Sees |
|---|---|
| Modeler | nothing (first) |
| Harness Builder | Modeler's constraints |
| Scenario Designer | all constraints |
| Investigator | all constraints, own cluster detail, peers' `observation`s |
| Fixer | all constraints, own cluster's `root_cause`, Reviewer notes |
| Reviewer | everything |
| Reporter | confirmed findings only |

Two rules behind the table:

1. **Constraints are global.** Anything that would make another agent's work
   wrong must reach every agent. These are broadcast.
2. **Detail is local.** Cluster specifics go only to the agents working that
   cluster. Broadcasting everything degrades every agent's focus and costs
   context budget for no gain.

## Cost and concurrency

`MAX_PARALLEL_AGENTS` is enforced by a semaphore in `devin/client.py`, not in
the pipeline, so every path that creates a session is bounded — retries and the
Reviewer's follow-ups included.

Typical run: 1 harness + 1 designer + K investigators + K fixers + 1 reviewer +
1 reporter, with K capped at the cluster limit. A Modeler is added only on a
library miss. K is what clustering controls, which is why clustering earns its
own stage.

## Persistent knowledge

`devin/knowledge.py` keeps a small amount across runs, per repo:

* which Menagerie model matched, and the confidence;
* the harness adapter shape that worked (expensive to rediscover every run);
* confirmed `constraint` findings;
* known-flaky seeds.

Explicitly **not** kept: root causes of fixed bugs. Once patched they are noise,
and a stale cause actively misleads the next Investigator.
