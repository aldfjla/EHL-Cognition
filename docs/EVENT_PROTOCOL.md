# Event protocol

Everything the dashboard shows arrives as an `Event` over
`WS /ws/runs/{run_id}`. This document is the contract between
`orchestrator/bus.py`, `apps/api/app/routers/stream.py` and
`apps/ui/lib/useEventStream.ts`.

Schema: `packages/contracts/schemas/event.json`.

## Envelope

```jsonc
{
  "id":     "evt_9c1f22a4",       // unique
  "run_id": "run_4f9c2a11",
  "seq":    47,                    // monotonic within the run, assigned by the bus
  "type":   "scenario.finished",
  "ts":     "2026-08-22T14:03:11.482Z",
  "data":   { /* payload, per type below */ }
}
```

One event per WebSocket frame. No batching — at this event volume it would
complicate the client for nothing.

## Connection lifecycle

```
client                                    server
  │  GET /ws/runs/{id}?since=0              │
  │ ───────────────────────────────────────▶│
  │                                         │ replay buffered events with seq > since
  │ ◀───────────────────────────────────────│   (up to REPLAY_BUFFER_SIZE = 500)
  │                                         │ then stream live
  │ ◀───────────────────────────────────────│
  │            ...                          │
  │ ◀────────── run.finished ───────────────│
  │ ◀────────── close(1000) ────────────────│
```

**Reconnect:** the client tracks the highest `seq` it applied and reconnects
with `?since=<seq>`. Nothing is lost as long as the gap is inside the replay
buffer.

**Gap detection:** if an arriving event's `seq` is more than one past the last
applied, the client fell behind. It must **refetch `GET /runs/{id}`** and
resync rather than trying to reconstruct state from a partial stream. Silently
continuing produces a dashboard that is subtly wrong, which is worse than one
that visibly reloads.

**Backpressure:** a subscriber whose queue fills is dropped by the bus and the
socket is closed with code `1013` (try again later). The pipeline is never
blocked by a slow browser tab.

**Heartbeat:** the server sends a ping every 30s. Idle WebSockets get closed by
intermediaries, and a frozen dashboard is indistinguishable from a hung run.

## Idempotency

Every consumer must be idempotent. Replay after reconnect will redeliver events
the client already applied, so:

* upsert by `id` for `agent.*`, `scenario.*`, `finding.*`;
* replace wholesale for `run.*` and `report.*`;
* append for `message.sent`, deduped on `id`.

`applyEvent()` in `useEventStream.ts` is a pure function for exactly this
reason — it can be unit-tested and replayed.

## Event types

### Run lifecycle

| type | when | `data` |
|---|---|---|
| `run.created` | webhook accepted | full `Run` |
| `run.stage_changed` | every legal transition | `{stage, previous_stage}` |
| `run.finished` | terminal stage reached | full `Run` |

`run.stage_changed` is a **partial patch**, not a full object — it fires often
and the client already holds the run. The UI drives `PipelineTimeline` from it.

### Agents

| type | when | `data` |
|---|---|---|
| `agent.created` | before the Devin session is requested | full `Agent` |
| `agent.status_changed` | any status transition | `{agent_id, status, previous_status}` |
| `agent.activity` | new transcript line | `{agent_id, text, ts}` |

`agent.created` is emitted **before** the API call, with `status: "starting"`
and a null `session_id`. A card appearing instantly and then filling in reads as
alive; a five-second blank grid reads as hung.

`agent.activity` is the highest-volume event type. It is throttled server-side
to at most one per agent per second — the ticker exists to show *that* work is
happening, and a faster feed is unreadable anyway.

### Messages

| type | when | `data` |
|---|---|---|
| `message.sent` | orchestrator relays a finding | full `Message` |

Emitted at the moment of the relay — when the finding is written into the
recipient's prompt, not before. `TeamChat` and `AgentGraph` both consume this.

### Scenarios and the suite

| type | when | `data` |
|---|---|---|
| `scenario.created` | matrix generated | full `Scenario` (status `pending`) |
| `scenario.started` | worker picks it up | `{scenario_id}` |
| `scenario.finished` | result available | full `Scenario` |
| `suite.progress` | every N completions | `{total, completed, passed, failed}` |

The whole matrix is emitted as `scenario.created` up front, so
`ScenarioMatrix` can render the full grid greyed out and fill it in. Growing
the grid cell by cell hides the scale of what is being tested, which is one of
the things worth showing.

`suite.progress` is redundant with counting `scenario.finished` events. It
exists so the index page and any late subscriber get a summary without
replaying the whole matrix.

### Findings

| type | when | `data` |
|---|---|---|
| `finding.created` | written to the blackboard | full `Finding` |
| `finding.updated` | confirmed / refuted / superseded | `{finding_id, status, superseded_by}` |

### Artifacts and report

| type | when | `data` |
|---|---|---|
| `artifact.created` | video/diff/report file written | `{kind, path, scenario_id?, run_id}` |
| `report.created` | REPORT completes | full `Report` |

`path` is relative to `ARTIFACTS_DIR`; the client resolves it with
`api.artifactUrl()`.

### Errors

| type | when | `data` |
|---|---|---|
| `error` | infrastructure failure | `{stage, message, fatal}` |

**`error` never means the robot failed a test.** A failing scenario is a
`scenario.finished` with `status: "failed"` — a completely normal, expected
outcome. `error` means *our* system broke: a Devin session errored, the sim
crashed, the repo would not clone. Conflating the two is the fastest way to
destroy a user's trust in a CI system, so they are separate types and the UI
renders them completely differently.

`fatal: true` means the run is landing in `FAILED_UNRESOLVED`.

## Component subscriptions

One hook (`useEventStream`) holds all state; components read from it. No
component opens its own socket — N sockets would mean N reconnect policies and
components disagreeing about `seq`.

| Component | Consumes |
|---|---|
| `PipelineTimeline` | `run.stage_changed`, `run.finished` |
| `AgentGrid` / `AgentCard` | `agent.*` |
| `TeamChat` | `message.sent` |
| `AgentGraph` | `agent.created`, `message.sent` |
| `ScenarioMatrix` | `scenario.*`, `suite.progress`, cluster data from `GET /runs/{id}` |
| `DiffViewer` / `ReportView` | `report.created` |
| `VideoCompare` | `artifact.created`, `report.created` |

## Versioning

Additive changes (a new optional field, a new event type the UI ignores) need no
coordination. Renaming a field, removing one, or adding an enum member the UI
switches on is breaking: update `event.json`, `schemas.py` and `types.ts` in the
same commit, and note it here.

## Testing without a backend

`scripts/seed_mock_run.py` replays a scripted event sequence with realistic
timing. The entire dashboard can be built and demoed against it before the
pipeline exists. Seeded runs are prefixed `[REPLAY]` in the title — a replay
must never be presentable as a live run.
