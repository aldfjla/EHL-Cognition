---
name: testing-dashboard-runs
description: How to run the Robot CI stack locally and trigger a real run from the dashboard (repo connect + "Run analysis"), including the GitHub token traps for private customer repos, how to wire real Devin credentials, and how far a run can actually get (BUILD_HARNESS is the wall).
---

# Testing dashboard-triggered runs (connect repo + "Run analysis")

## Bring the stack up

```bash
cd /home/ubuntu/repos/EHL-Cognition
cp -n .env.example .env                 # DATABASE_URL=sqlite:///./robotci.db
setsid nohup make api > /tmp/api.log 2>&1 < /dev/null &   # :8000
(cd apps/ui && setsid nohup npm run dev > /tmp/ui.log 2>&1 < /dev/null &)  # :3000
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/health
```

The dashboard (repository cards, run list) is at `http://localhost:3000/runs`, not `/`.
`+ Connect repository` opens a modal: paste the repo URL, keep `Watched branch`, click
`Connect`; the card then exposes `Run analysis` and `disconnect`.

Gotchas seen repeatedly:
- Killing the API: `make api` runs uvicorn `--reload`, whose worker is a
  `multiprocessing.spawn` child that survives `pkill -f "uvicorn app.main:app"` and keeps
  port 8000, which makes the next `make api` die with `[Errno 98] Address already in use`.
  Find the real owner with `ss -ltnp | grep :8000` and `kill -9` that pid.
- Do not run `pkill -f uvicorn` from the exec tool: the pattern matches the tool's own
  `bash -c` command line and kills your shell. Use `pkill -f "[u]vicorn"`.
- To reset the dashboard to a clean state (no repos, no runs) delete `robotci.db` while the
  API is stopped, then restart it.
- Reconnecting an already-connected repo returns `409 repository already connected`; for a
  negative test connect a nonexistent `owner/name` instead. The connect modal keeps a stale
  inline error after you edit the fields, and the first `Connect` click after an error is
  sometimes swallowed — click it again.

## GitHub tokens (the part that wastes the most time)

`manual_trigger` resolves the branch head server-side when the client sends no `sha`
(`orchestrator/github.py: branch_head`, httpx + `GITHUB_TOKEN`), and `workspace.clone` runs
`gh repo clone` (auth from `GH_TOKEN`/`GITHUB_TOKEN`). For a **private customer repo** both
of those need a token that can actually read it:

- `gh auth token` returns the box's *default* installation token, which may 404 on the
  customer repo even though `gh api repos/<owner>/<repo>/...` succeeds. The reason is that
  `gh` on Devin boxes is a wrapper (`/opt/.devin/package/custom_binaries/gh`) that picks a
  per-owner token out of `/opt/.devin/.devin-integration-gh-credentials` (lines are
  `github.com/<owner> <token>`), choosing by `--repo` or by the **cwd's** git remote.
- So export the owner-scoped token explicitly:
  `GITHUB_TOKEN=$(grep '^github.com/<owner> ' /opt/.devin/.devin-integration-gh-credentials | cut -d' ' -f2)`
  and `GH_TOKEN="$GITHUB_TOKEN"`.
- That is still not enough for the clone: the wrapper *overwrites* `GH_TOKEN` based on the
  cwd's remote (this repo → wrong owner). Put the real binary first on the API's PATH:
  `mkdir -p /tmp/ghbin && ln -sf /usr/bin/gh /tmp/ghbin/gh && export PATH=/tmp/ghbin:$PATH`.
- Verify before recording: `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<repo>/commits/main` must be 200.
- A fine-grained PAT owned by a *different* user cannot read another user's private repo at
  all (always 404), so such a secret is useless here no matter how it is passed. Also check
  whether a provided secret's value is a whole `NAME=value` env line rather than a bare
  token — binding it verbatim yields `401 Bad credentials`.
- Consider `ROBOTCI_DRY_RUN=1` for demos: outbound writes (commit statuses, branches, PRs)
  into the customer's repo are logged instead of performed.

## How far a run gets, and what it needs

Sequence: `TRIGGERED → RESOLVE_MODEL → BUILD_HARNESS → DESIGN_SCENARIOS → RUN_SUITE → …`.

- A repo with no `robotci.yaml` `control.entrypoint` fails in ~1s with
  `PipelineError: robotci.yaml has no control.entrypoint …`. Never pick such a repo for a
  demo of "the run progresses".
- `RESOLVE_MODEL` succeeding (card/run page shows e.g. `robotstudio_so101 · 6 dof`,
  provenance `robotci.yaml robot.menagerie=…`) is good proof the private-repo clone worked.
- `BUILD_HARNESS` unconditionally dispatches a Devin agent, so **without `DEVIN_API_KEY`
  every run dies ~2s in** and the run page renders as a closed snapshot — no ticking
  `elapsed` timer, no workers/scenario tiles. There is no offline/fake agent mode in the
  code. If a test asks for visible live progression, request `DEVIN_API_KEY` (plus
  `DEVIN_ORG_ID` for v3 orgs) up front instead of discovering this mid-recording.
- Symptom to expect in that case (arguably a bug worth reporting rather than a setup
  issue): the UI shows `Infrastructure error: AttributeError: 'NoneType' object has no
  attribute 'create_session'` instead of `DEVIN_API_KEY unset; copy .env.example to .env`.

## With real Devin credentials

Wire them into the API **process env** (pydantic `Settings` reads env vars and `.env`; the
orchestrator also reads `os.getenv`). Keep them off the screen/history by putting them in a
chmod-600 file and sourcing it:

```bash
# /home/ubuntu/.robotci-demo.env  (export DEVIN_API_KEY=… DEVIN_ORG_ID=… DEVIN_API_BASE=https://api.devin.ai/v3)
set +o history; . /home/ubuntu/.robotci-demo.env && make api
curl -s localhost:8000/ready   # must show "devin_api_key": true
```

Validate the key before recording — `GET https://api.devin.ai/v3/sessions` is 404 even for a
good key; the org-scoped path is the real one:
`GET https://api.devin.ai/v3/organizations/$DEVIN_ORG_ID/sessions?limit=1` → 200.

What then happens (observed, may still be true):
- `BUILD_HARNESS` dispatches a real Devin session; the run page shows a genuinely ticking
  `elapsed` timer, `1 agents working`, and the Agents tab links `open Devin session ↗`. The
  agent takes ~8 minutes.
- **The run still fails there**: `pipeline.py` `stage_build_harness` asserts
  `artifacts/<run_id>/harness.py` exists on the *orchestrator's* disk, but the Devin session
  runs in its own VM and returns the file only as an `ATTACHMENT:{"url":…}` in its final
  message (visible via `GET /runs/<id>/agents`). Result:
  `PipelineError: Harness Builder wrote no harness at artifacts/<run>/harness.py (agent
  status succeeded, …)` → `FAILED_UNRESOLVED`, so `DESIGN_SCENARIOS`/`RUN_SUITE` and any
  MuJoCo scenario/live-frame activity never happen. `ROBOTCI_DRY_RUN` is irrelevant to this
  (it only gates GitHub statuses/branches/PRs in `github.py`). If a task asks for a demo of
  the physics solver / live scenario tiles, check first whether artifact retrieval from the
  agent has been implemented; otherwise expect to be blocked and escalate early.
- Long stages: the run page's websocket drops and the page shows
  `Stream closed. This view is a snapshot, not live.` — refresh (F5) to resume updates;
  don't read it as "the run ended".
- The dashboard run-list row can stay at `model pending / TRIGGERED` after a run reaches a
  terminal state (the repo card's `latest run` is correct).

## What a full run looks like (timings, as of PR #57)

With real Devin creds, the private customer repo `krishaanth5831/robot-ci-test@main` gets:
`TRIGGERED`/`RESOLVE_MODEL` (seconds) → `BUILD_HARNESS` **~9-10 min** (one real Devin session
writes the adapter; since PR #57 the source arrives as the `harness_code` field of the agent's
structured output and the orchestrator writes it to `artifacts/<run_id>/harness.py` itself) →
local smoke scenario → `DESIGN_SCENARIOS` ~3 min → `RUN_SUITE` (4 workers, 5 scenarios).
Budget ~20 min of wall clock per run and poll from the shell rather than watching the browser:

```bash
curl -s localhost:8000/runs/<run_id> | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['stage'],d['status'])"
ls -la artifacts/<run_id>/            # harness.py appearing == BUILD_HARNESS passed
curl -s localhost:8000/runs/<run_id>/scenarios | head -c 400
grep -iE "harness|result callback|failed in" /tmp/api.log | tail
```

`[harness] N joint-position commands were outside the actuator ctrlrange…` lines in the API log
are proof that MuJoCo really stepped the customer's controller.

## Known runtime failure at the end of RUN_SUITE

`simkit.scoring` puts a `measured` key in each criterion dict, but
`orchestrator.schemas.CriterionResult` forbids extras, so
`pipeline.py::_apply_result` raises
`pydantic ValidationError: measured — Extra inputs are not permitted` for every scenario and the
run dies in `RUN_SUITE` with `FAILED_UNRESOLVED`, `0 passed / 0 failed`. If you see that, the
physics ran fine and the failure is infra — do not report it as a robot-code verdict. It may
already be fixed; check whether `CriterionResult` has a `measured` field before blaming it.
Also expect `no frames from worker` / `0 streaming` tiles: live frame streaming did not produce
frames in any observed local run.

## Known runtime walls seen so far (each fixed one exposed the next)
1. `BUILD_HARNESS` "wrote no harness" — fixed by transferring `harness_code` (PR #57).
2. `CriterionResult` / `measured` extra-field ValidationError on score ingestion — fixed by
   mapping `measured`→`value` (commit 0dd4e27). After that, per-criterion values render in the
   Scenarios tab (e.g. `fail object_in_bin · 0.1594 / 0.09`).
3. Live frames — after the PR #51 live-frame merge, tiles DO stream (`N streaming` > 0, rendered
   MuJoCo images). One tile can still say `no frames from worker` when two scenarios share a
   worker slot (e.g. two scenarios on `w3`).
4. FIX stage: `cluster <label> failed: 'str' object has no attribute 'detail'` →
   run error `every Fixer failed before producing a patch`, terminal `FAILED_UNRESOLVED`.
   Cause: `pipeline.py` `_fix_cluster` passed `root_cause=work.cause.summary` (a str) while
   `roles/fixer.py` does `cause.detail or cause.summary`. FIXED by commit 968119d
   (`root_cause=work.cause`). After that the Fixer really is dispatched.
4b. Next wall at FIX: the run STILL ends `every Fixer failed before producing a patch` even
   though the Fixer returned a good diff. `_fix_cluster` (pipeline.py ~776) bails on
   `if "patched" in role.output and not role.output["patched"]` BEFORE looking at `patch`, and
   the Fixer sets `patched:false` whenever the cluster's seeds don't all go green (partial fix).
   To diagnose, read the agent's structured output straight from SQLite — the API/UI do not
   expose it:
       sqlite3 robotci.db "select last_activity from agents where role='fixer'"
   the trailing ```json block has `patched`, `patch`, `cluster_seeds_passing`. Extract `patch`
   and sanity-check it with `git apply --check` in a fresh clone of the target repo.
   Fixing this likely means applying/verifying a non-empty `patch` regardless of `patched`,
   or defining `patched` as "diff produced" rather than "all seeds green".
4c. Wall seen on PR #58 (branch `devin/1787460178-apply-partial-fixes`): BUILD_HARNESS dies with
   `PipelineError: harness smoke test errored: harness: harness.py does not expose a callable
   run_episode`. Cause: the harness-builder agent's *structured output* `harness_code` came back
   as the literal placeholder from the prompt's example block
   (`# the complete harness module source, JSON-escaped`, 50 bytes), and `stage_build_harness`
   writes any non-empty `harness_code` verbatim. Check it immediately with
   `wc -c artifacts/<run>/harness.py` — anything under a few KB is a placeholder/truncated blob,
   not a harness. Meanwhile the Devin session itself may have built a real harness (10-20 KB) and
   returned it only as a session *attachment*, so the agent's report reads like success. Reproduced
   on two consecutive runs, so it is not a one-off. Possible directions: validate `harness_code`
   (must define `def run_episode`) before writing, refuse the prompt's example string, or fall
   back to fetching the session attachment. Because this fires first, PR #58's new
   `sim_time_s <= 0.0` gate and its partial-patch apply path are unreachable.
5. Scenario replay video 404s: the UI requests
   `/artifacts/artifacts/<run>/<scenario>.mp4` (doubled `artifacts/`) which 404s, while
   `/artifacts/<run>/<scenario>.mp4` returns 200 — tile shows "recording unavailable".

Timings for a full run against `krishaanth5831/robot-ci-test@main`: ~10 min BUILD_HARNESS,
RUN_SUITE around 13-17 min elapsed, INVESTIGATE finishes ~23 min. Poll with
`curl -s localhost:8000/runs/<id>` and `grep -iE "cluster|Traceback|patch" <api log>` rather
than watching the browser; refresh the run page (F5) for screenshots since the stream drops.

## Devin Secrets Needed

- `DEVIN_API_KEY` (and `DEVIN_ORG_ID` if the org is on v3) — required for any run to pass
  `BUILD_HARNESS` and therefore for any demo of live stage/worker progression.
- A GitHub credential with read access to the target private repo, if the box's
  owner-scoped integration token does not already cover it.
