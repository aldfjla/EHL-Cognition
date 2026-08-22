# Dormancy and cold start — measured

The product claim is that between runs Robot CI is asleep: no polling loop of
ours, no idle worker process, no Devin session, no MuJoCo process. This document
is the evidence, not the assertion. Every number below came out of
`scripts/measure_dormancy.py`; nothing here is estimated, and the things this
environment cannot measure are named as unmeasured rather than filled in.

Reproduce with:

```bash
.venv/bin/python scripts/measure_dormancy.py --idle-seconds 120
```

The script boots a real uvicorn process against a throwaway SQLite file, samples
`/proc/<pid>` after boot and again after the idle window, then subscribes to
`WS /ws/runs` and delivers one HMAC-signed push, timing the first event to reach
that subscriber.

## What the code audit found

| candidate | verdict |
|---|---|
| module import (`app.main`, `app.deps`, routers, `orchestrator.*`) | starts nothing — no task, thread or process. Asserted in `apps/api/tests/test_dormancy.py` |
| app startup (`deps.lifespan`) | creates the `EventBus` object, runs `init_db()`, and constructs a `DevinClient` **only** if `DEVIN_API_KEY` is set. No `create_task`, no scheduler, no pool |
| `DevinClient` | an `httpx.AsyncClient` and a semaphore; connections are opened per request. It never polls unless a run is waiting on a session |
| `SuitePool` / `ProcessPoolExecutor` | constructed inside `Pipeline`, per run — never at import or startup. `ProcessPoolExecutor` also spawns workers lazily on first submit, and `aclose()` shuts them down at the end of the run |
| MuJoCo | only ever imported inside the worker process entrypoint (`orchestrator/pool.py::_run_simkit`), so an idle API process has never loaded it |
| `EventBus` | passive; fan-out happens on the publisher's task. Its replay buffers are evicted when a run closes |
| `WS /ws/runs*` heartbeat (30 s) | only while a browser is connected, and it is a `wait_for` timeout on the subscriber iterator, not a second task per socket |
| `GET /runs/{id}/live` (MJPEG) | polls for new frames only while a client is pulling the stream, and has its own idle timeout |
| `orchestrator/devin/client.py`, `pipeline.py`, `pool.py` `while True` loops | all inside a run: session warm-up, session polling, progress ticks. None outlives its run |

Nothing had to be fixed to make the claim true; what was missing was proof, so
the invariants are now tests (`apps/api/tests/test_dormancy.py`) that fail if a
future change schedules work at import, in the lifespan, or on a probe request.

## Idle — measured

One uvicorn worker, no run in flight, no dashboard connected, 120 s window
(Linux, CPython 3.12.8):

| | at settle | after 120 s idle |
|---|---|---|
| RSS | 75.8 MB | 75.8 MB |
| threads | 6 | 6 |
| child processes | **0** | **0** |
| open sockets | 3 | 3 |
| CPU consumed during the window | — | **0.09 s** ≈ 0.075 % of one core |

Boot to `/health` answering: **0.61 s**. Five seconds after a run had finished:
78.2 MB RSS, 7 threads, **0 child processes** — the run left no worker behind.

Two honest caveats about those numbers:

* The 6 idle threads are the anyio worker-thread pool uvicorn/Starlette keep for
  sync endpoints, plus the main thread. They are parked, not looping.
* The 0.075 % CPU is not zero because **uvicorn's own `Server.main_loop` wakes
  every 100 ms** to check its shutdown flag. That is a polling loop in the
  server we run on, not in Robot CI, and it is the only periodic wake-up
  observable at idle. It is reported here rather than rounded away.
* RSS is dominated by the imports FastAPI/SQLModel/httpx pull in. MuJoCo is not
  among them — it is only imported in worker processes, which is why there is a
  75 MB idle process and not a 400 MB one.

## Cold start — measured

Time from writing the first byte of a signed push delivery to an already-
connected subscriber receiving an event:

| | |
|---|---|
| HTTP response to GitHub | **27.9 ms** |
| first event on the subscriber (`run.created`) | **28.0 ms** |
| first pipeline work observed on the run's socket | **253 ms** |

Target was under ~10 s; measured **0.028 s** to the first event, and 0.25 s
until the pipeline had entered `TRIGGERED` and reported on the checkout attempt.

### What this measurement does *not* cover

The push was delivered for `acme/robot`, which does not exist, and
`DEVIN_API_KEY` was empty. So the 253 ms mark is the pipeline reaching the clone
and reporting the failure as an infrastructure error — the honest read is
"the system was awake and doing real work 0.25 s after the push". It is **not**
a measurement of a full green suite. Not measured here, and not estimated:

* clone + `robotci.yaml` read time for a real customer repo (needs a real repo
  and outbound GitHub access from this environment);
* Devin session creation latency and agent stage durations (needs a live
  `DEVIN_API_KEY`);
* MuJoCo worker start-up and per-scenario simulation time (needs the Menagerie
  model library downloaded via `make menagerie`);
* multi-worker or multi-host behaviour — the bus is in-process by design, so
  there is nothing to measure until there is a second process.

Anyone with those three things can rerun the same script against a real repo;
the method above is exactly what produced the numbers in this file.
