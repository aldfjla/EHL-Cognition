# contracts

The single source of truth for every shape that crosses a process boundary in
Robot CI. Three consumers read these definitions, and all three must agree:

| Consumer | File | Relationship |
|---|---|---|
| Orchestrator (Python) | `packages/orchestrator/orchestrator/schemas.py` | Pydantic models, hand-mirrored |
| API (Python) | `apps/api/app/store/tables.py` | SQLModel tables, persist a subset |
| Dashboard (TypeScript) | `apps/ui/lib/types.ts` | TS interfaces, hand-mirrored |

## Schemas

| File | What it is |
|---|---|
| `run.json` | One CI run — a push taken from trigger to terminal state |
| `repo.json` | One GitHub repository connected to Robot CI |
| `agent.json` | One Devin session wrapped in a team role |
| `message.json` | One orchestrator-mediated relay between agents |
| `scenario.json` | One randomized world + its pass/fail result |
| `finding.json` | One unit of knowledge on the shared blackboard |
| `report.json` | The written incident report / PR body |
| `event.json` | The WebSocket envelope carrying all of the above |

`event.json` is not in the original spec list but is required: it is the
envelope that `stream.py` emits and `useEventStream.ts` consumes, and
`EVENT_PROTOCOL.md` is written against it.

## How TS types stay in sync

**Today: by hand, deliberately.** `apps/ui/lib/types.ts` is written to mirror
these files one-for-one, with the same field names and the same enum members.
The header comment in `types.ts` names the schema each interface tracks.

This is a hackathon-scale choice with a real justification: the type surface is
seven objects, it changes as a unit, and a codegen step is one more thing to
break on stage at 3am. Hand-mirroring costs about ten minutes total; a broken
generator costs the demo.

**When it stops being fine:** the moment a third client appears, or the moment
someone edits `types.ts` without editing the schema. At that point add:

```bash
npx json-schema-to-typescript -i 'packages/contracts/schemas/*.json' \
  -o apps/ui/lib/types.generated.ts
```

and make `types.ts` re-export from it. Pydantic models can be generated the
same way with `datamodel-code-generator`. Neither is wired up in this pass.

## Rules for changing a schema

1. Edit the `.json` first. It is the spec; the mirrors are downstream.
2. Update `schemas.py` and `types.ts` in the *same* commit. A schema change
   that lands alone is how the WebSocket starts silently dropping fields.
3. Additive changes (new optional field) need no version bump. Renaming or
   removing a field, or adding an enum member the UI switches on, does —
   bump `version` in the run payload and note it in `docs/EVENT_PROTOCOL.md`.
4. Every field carries a `description`. These schemas double as documentation
   for the Devin sessions that read them.

<!-- TODO(build): decide whether to adopt codegen before the type surface grows past ~10 objects. -->
