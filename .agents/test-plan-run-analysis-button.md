# Test plan — PR #55 "Run analysis" button (branch devin/1787449665-run-analysis-button)

Environment (already set up, not part of the plan): API on :8000 (`make api`, cwd
/home/ubuntu/repos/EHL-Cognition, .env copied from .env.example, DEVIN_API_KEY empty),
UI dev server on :3000, Chrome maximized. DB currently has zero connected repos.

RUN 7 (current): branch `devin/1787460178-apply-partial-fixes` @ d3b679a (PR #58, off main
which already has PR #57). Same env (real Devin creds, owner-scoped GITHUB_TOKEN, /usr/bin/gh
first on PATH, ROBOTCI_DRY_RUN=1); API restarted, robotci.db + artifacts/run_* wiped, /ready ->
devin_api_key: true, UI 200.
Deltas under test:
 - `_fix_cluster` (pipeline.py:776-786) now applies ANY non-empty `patch`; `patched:false` only
   short-circuits when the diff is empty. Cluster seeds then rerun and the Reviewer judges.
 - `stage_build_harness` (pipeline.py:485-489) raises
   `harness smoke test never advanced the simulation (sim_time_s=...)` when the smoke run has
   sim_time_s <= 0.0.
Assertions in order:
 A. (brief) Run analysis -> /runs/{id}, header `@ 300953f`, timer ticks.
 B. BUILD_HARNESS: either harness.py materialises AND the smoke run has sim_time_s > 0, or the
    run fails LOUDLY with the new message naming the agent session (adversarial: RUN 6's
    degenerate harness silently produced sim_time 0.0 for all scenarios — that must not happen
    silently now).
 C. RUN_SUITE: scenarios have sim_time_s > 0 and per-criterion values; live wall shows
    `N streaming` > 0 with rendered frames (only checkable if the sim really steps).
 D. FIX: Fixer dispatched; its diff is APPLIED — evidence: `workspace.apply_patch` invoked
    (no `patch apply` nonfatal), the fix worktree's src file differs from HEAD, and the
    cluster's seeds re-execute after the patch (repeat runs for the same seeds).
 E. VERIFY/REPORT do real work: Reviewer agent dispatched with before/after stats and a diff;
    cluster phase reaches verifying/resolved (or a reasoned still-red).
 F. Terminal verdict is a genuine robot-code result with counts, and the run error is NOT
    `every Fixer failed before producing a patch`. Capture verbatim.
 G. Note whether the known secondary bugs reproduce: "Stream closed", stale run-list row,
    `/artifacts/artifacts/...mp4` 404.

RUN 6 (previous): branch `local-demo-integration` @ 57eb34d (RUN 5 tree + commit 968119d:
`_fix_cluster` now passes `root_cause=work.cause`, the Finding object, instead of
`work.cause.summary`, so `roles/fixer.py:43` `cause.detail or cause.summary` no longer raises
`'str' object has no attribute 'detail'`). Same env (real Devin creds from
/home/ubuntu/.robotci-demo.env, owner-scoped GITHUB_TOKEN, /usr/bin/gh first on PATH,
ROBOTCI_DRY_RUN=1); API restarted, robotci.db + artifacts/run_* wiped, /ready ->
devin_api_key: true, UI 200.
Focus: the previously-untested tail. Assertions in order:
 A. (brief) Run analysis -> disabled "Starting…" -> /runs/{id}, header `@ 300953f`.
 B. (brief) BUILD_HARNESS completes: artifacts/<run>/harness.py exists locally, stage green.
 C. (brief) RUN_SUITE: live tiles show rendered frames with `N streaming` > 0, timer ticks,
    worker/scenario counters change, scenarios land on passed/failed with per-criterion values
    (no `ValidationError` / `result callback failed` in the API log).
 D. FIX dispatches REAL Fixer agents: Agents tab shows a Fixer session per cluster and the API
    log contains NO `'str' object has no attribute 'detail'` (adversarial: that exact string
    must be absent, and `every Fixer failed before producing a patch` must NOT be the run error).
 E. Each Fixer returns a `patch` unified diff and it is applied to the local worktree:
    evidence = workspace apply_patch in the log / a non-empty `diff` in the cluster evidence /
    modified files under workspaces/<run>/..., shown in the UI cluster panel.
 F. The cluster's seeds RERUN after the patch (repeat scenario executions for the same seeds,
    cluster phase moves past `fixing` to verifying/resolved), then VERIFY/REPORT run.
 G. Terminal verdict is a genuine robot-code result with counts (e.g. `n passed / m failed`,
    resolved or still-red) and NOT `Failed — infrastructure error`. Capture verbatim.
 H. Note whether the known secondary bugs reproduce: run-page "Stream closed" mid-run, stale
    dashboard run-list row, `/artifacts/artifacts/...mp4` 404 ("recording unavailable").

RUN 5 (previous): branch `local-demo-integration` (c991d87 = PR #57 aa9895a + scoring fix 0dd4e27
+ crash/live-frame fix 02bd0a3). Same env as RUN 4 (real Devin creds, owner-scoped GITHUB_TOKEN,
/usr/bin/gh first on PATH, ROBOTCI_DRY_RUN=1); API + UI restarted from this branch, robotci.db
and artifacts/run_* wiped, /ready -> devin_api_key: true.
Deltas under test: CriterionResult now maps simkit's `measured` -> `value` and accepts `detail`
(schemas.py:273-290) so score ingestion should no longer raise ValidationError; simkit live.py /
runner.py + pool.py + webhooks.py changes should make live frames appear and reconcile state on
fatal crashes.
Assertions (in order):
 A. Run analysis -> disabled "Starting…" -> /runs/{id}, header `@ 300953f`.
 B. BUILD_HARNESS completes: artifacts/<run>/harness.py exists locally, stage turns green.
 C. RUN_SUITE ingests scores: scenarios leave `running` and land on passed/failed with per-
    criterion values (adversarial: last run every scenario stuck at `running`, 0 passed/0 failed,
    and the sidebar showed `ValidationError ... measured ... extra_forbidden` — that exact string
    must NOT appear, and `grep "result callback failed" api log` must be empty).
 D. Live frames: at least one tile in "Live simulations" shows an actual rendered frame and the
    header reports `N streaming` > 0. If not: check artifacts/<run>/frames|*.jpg|png and the
    frame HTTP endpoint and record the exact reason.
 E. Timer ticks; workers and running/passed/failed counters change over time.
 F. If failures cluster: CLUSTER_FAILURES -> INVESTIGATE -> FIX -> VERIFY, with a Fixer `patch`
    applied locally (log: apply_patch / cluster rerun) and the cluster re-executed.
 G. Terminal verdict is a genuine robot-code result (counts) or an explicitly labelled infra
    error, captured verbatim.
 H. Note whether the two known secondary bugs reproduce: run-page "Stream closed" mid-run and a
    stale dashboard run-list row after completion.

RUN 4 (previous): branch devin/1787452617-remote-agent-artifacts (aa9895a, PR #57). Same env as
RUN 3 (real Devin creds in the API process, owner-scoped GITHUB_TOKEN, /usr/bin/gh first on
PATH, ROBOTCI_DRY_RUN=1, robotci.db reset, /ready -> devin_api_key: true). Delta under test:
`stage_build_harness` now materialises the agent's `harness_code` to
artifacts/<run>/harness.py locally (pipeline.py:459-470) before the smoke test, and the Fixer
applies its `patch` diff via workspace.apply_patch (pipeline.py:770-780).
Assertions for this run, in order:
 A. Run analysis -> disabled "Starting…" -> /runs/{id}, header `@ 300953f` (real head of main).
 B. BUILD_HARNESS COMPLETES: pipeline sidebar moves past BUILD_HARNESS, and
    artifacts/<run_id>/harness.py exists on THIS box (adversarial check: the file could not
    exist unless harness_code came back through structured output), smoke scenario executed.
 C. DESIGN_SCENARIOS + RUN_SUITE actually simulate: scenario counters leave 0 (running/queued/
    passed/failed change), workers > 0, scenario tiles / live frames visible in "Live
    simulations", Scenarios tab non-empty.
 D. elapsed timer increments across screenshots while stages advance.
 E. Terminal verdict reached and rendered; a red verdict is acceptable only if it comes from
    the robot code (criteria failures), NOT from infra (PipelineError / Infrastructure error).
 F. If the agent omits harness_code again -> capture the structured output and escalate, no
    blind retries.

RUN 3 (previous): same flow as RUN 2 (real private repo krishaanth5831/robot-ci-test@main,
owner-scoped GITHUB_TOKEN, /usr/bin/gh first on PATH, ROBOTCI_DRY_RUN=1) but now with REAL
Devin credentials in the API env (DEVIN_API_KEY / DEVIN_ORG_ID / DEVIN_API_BASE=.../v3;
verified out-of-band: GET /v3/organizations/{org}/sessions -> 200; /ready reports
devin_api_key: true). DB reset, API restarted. Expectation this time: the run gets past
BUILD_HARNESS, dispatches real Devin agent sessions, runs actual MuJoCo scenario sims
(mujoco 3.12.0 + vendor/menagerie/robotstudio_so101 present), so the run page should show a
ticking elapsed timer, advancing stages, worker/scenario tiles and live frames, and reach a
genuine terminal verdict. Assertions: busy state -> navigation -> header sha 300953f ->
timer increments across screenshots -> stage advances beyond BUILD_HARNESS -> scenario/worker
activity -> terminal verdict captured. Escalate (don't retry blindly) on Devin auth/API-base/
quota errors, with the exact API log line.

RUN 2 (previous): checkout updated to merged `main` (c751141, includes PR #56 where
`branch_head` uses httpx + `GITHUB_TOKEN` instead of the gh CLI). Demo target repo is now
the customer's real private repo `krishaanth5831/robot-ci-test`, branch `main`.
API restarted with `GITHUB_TOKEN=GH_TOKEN=$(gh auth token)` (the session secret
ROBOT_CI_TEST_GITHUB_PAT is an `aldfjla` fine-grained PAT and 404s on that repo) and
`ROBOTCI_DRY_RUN=1` so the demo makes no writes into the customer's repo.
Baseline head of krishaanth5831/robot-ci-test@main = 300953f23c01433a69b31b79252af3acbf0f9a21.
The repo contains a real `robotci.yaml` with `control.entrypoint`, so this run is expected to
progress through clone/config/suite/sim stages with a ticking elapsed timer before the agent
stages fail (DEVIN_API_KEY unset).

Run-2 steps: connect the repo in the UI -> click "Run analysis" -> assert busy state,
navigation to /runs/{id}, header sha == 300953f, then observe live progression (elapsed timer
incrementing across screenshots, stage changing, workers/scenario activity) without waiting
for completion.

RUN 1 (previous) target repo: `aldfjla/EHL-Cognition`, branch `main` (lead-approved
substitute at the time).

Code evidence for the path under test:
- `apps/ui/components/repos/RepositoriesSection.tsx:58-70` — `runAnalysis()` sets busy
  ("Starting…"), calls `api.triggerRun(repo.full_name, undefined, repo.branch)`, then
  `router.push(/runs/{run_id})`; inline error rendered at :112-114.
- `apps/ui/components/repos/RepositoriesSection.tsx:101-108` — the new button, label
  "Run analysis" / "Starting…", `disabled={runBusy}`.
- `apps/ui/lib/api.ts:229-243` — `triggerRun` omits `sha` when undefined.
- `apps/api/app/routers/webhooks.py:537-560` — `sha` now optional; when absent resolves
  `github.branch_head(repo, branch)`; 422 with "could not resolve head of {branch}" on failure.
- `packages/orchestrator/orchestrator/github.py:46-52` — `gh api repos/{repo}/commits/{branch} --jq .sha`.
- Run page header `apps/ui/app/runs/[runId]/page.tsx:177-207` — shows `{repo} @ {sha[:7]} · {commit_message}`
  and an `elapsed` RunTimer.

Baseline fact recorded before the test: real head of `aldfjla/EHL-Cognition@main`
(`gh api repos/aldfjla/EHL-Cognition/commits/main --jq .sha`) = captured at execution time
(currently `0b003bb`). This is the adversarial anchor — a broken/empty-sha implementation
could not display this exact sha.

## Test 1 (primary): Run analysis button starts a run with a server-resolved sha
1. Open http://localhost:3000/. Click "+ Connect repository".
2. Type `https://github.com/aldfjla/EHL-Cognition` into the URL field; leave Watched branch
   `main`; click "Connect".
   - PASS: a repo card appears reading `aldfjla/EHL-Cognition`, badge `dormant`, branch `main`,
     latest run `—`. FAIL: error text in the dialog or no card.
3. On the repo card, click the "Run analysis" button (right side of the card header row).
   - PASS: button label changes to "Starting…" while the request is in flight (capture a
     screenshot during this window; if too fast to capture, mark inconclusive, not passed).
4. Observe navigation.
   - PASS: URL becomes `http://localhost:3000/runs/<run_id>` with a non-empty run id and the
     page H1 shows that same run id. FAIL: stays on `/`, or an inline red error appears under
     the card header.
5. Verify the sha was resolved server-side (the actual behavior change).
   - PASS: the run page subtitle reads `aldfjla/EHL-Cognition @ <first 7 chars of the real
     main head captured in the baseline> · manual trigger`. FAIL/inconclusive: `—` for the
     sha, a different sha, or missing repo name.
   - Cross-check: the API log line for `POST /webhooks/manual` returns 200, and the run row's
     commit sha equals the baseline head.
6. Verify the run is live, not a static stub.
   - PASS: the `elapsed` timer increments between two screenshots taken ~8s apart, and the
     stage indicator shows a real pipeline stage (e.g. CLONE/SUITE/SIM…) that is not
     permanently `—`. FAIL: timer frozen at the same value and no stage.
7. Let it run ~15-20s. Expected and acceptable: the run may end in a failed/`FAILED_*` state
   at the agent stages because DEVIN_API_KEY is empty. Note whatever stage it reaches; do not
   wait for completion.

## Test 2 (negative control, proves the 422 path is real and messaged in UI)
1. Return to http://localhost:3000/, click "+ Connect repository", connect
   `aldfjla/EHL-Cognition` again but with Watched branch `no-such-branch-xyz`.
   (If duplicate connect is rejected, instead connect `aldfjla/does-not-exist-xyz` on `main`.)
2. Click "Run analysis" on that card.
   - PASS: no navigation happens, the button returns to the label "Run analysis" (not stuck on
     "Starting…"), and a red inline message appears under the card header mentioning
     `could not resolve head of` (or the 422 detail). FAIL: silent nothing, navigation to a
     broken run page, or button stuck disabled forever.
3. Clean up: disconnect the bogus repo card via "disconnect" and accept the confirm dialog.

Why this is not a vacuous test: step 5 pins the displayed sha to the independently obtained
real branch head, and Test 2 shows the error branch is wired to the UI — a stubbed or
hardcoded implementation would fail one of those two.
