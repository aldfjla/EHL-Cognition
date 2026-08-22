"""The system must be asleep between runs.

The product claim is that with no run in flight there is no polling loop, no
idle worker process, no Devin session and no MuJoCo process. These tests pin
the parts of that claim that are observable in-process: nothing is scheduled,
spawned or connected by importing the app, by running its lifespan, or by
serving a request. The measured resource and cold-start numbers live in
``docs/DORMANCY.md``; this file guards the invariant they were measured under.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from app import deps
from app.main import create_app


def _own_children() -> set[int]:
    """PIDs of this process' children, or an empty set off Linux."""
    task_dir = Path("/proc/self/task")
    if not task_dir.is_dir():  # pragma: no cover - non-Linux CI
        return set()
    children: set[int] = set()
    for task in task_dir.iterdir():
        try:
            listing = (task / "children").read_text()
        except OSError:  # pragma: no cover - thread exited mid-read
            continue
        children.update(int(pid) for pid in listing.split())
    return children


def test_importing_the_app_starts_nothing() -> None:
    """No pool, thread or task may be created as an import side effect."""
    before = threading.active_count()
    children = _own_children()

    create_app()

    assert threading.active_count() == before
    assert _own_children() == children


def test_lifespan_creates_no_background_task_and_no_devin_session(
    client: TestClient,
) -> None:
    """Startup wires singletons; it does not start work.

    The ``client`` fixture has already run the lifespan by the time the body
    executes, so anything periodic would be visible here.
    """
    assert deps._devin is None  # no DEVIN_API_KEY in the test env

    async def observe() -> set[str]:
        with TestClient(create_app()) as inner:
            inner.get("/health")
            return {
                task.get_coro().__qualname__  # type: ignore[union-attr]
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            }

    assert asyncio.run(observe()) == set()


def test_health_and_ready_touch_no_worker(client: TestClient) -> None:
    """The probes the dashboard polls must not wake the simulation layer."""
    children = _own_children()
    threads = threading.active_count()

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200

    assert _own_children() == children
    assert threading.active_count() == threads


def test_an_ignored_push_leaves_no_task_behind(client: TestClient) -> None:
    """A filtered push must be cheaper than a run, not just quieter."""
    from tests.test_webhooks import commits, push_payload, signed

    children = _own_children()
    body, headers = signed(push_payload(commits=commits("README.md")))

    assert (
        client.post("/webhooks/github", content=body, headers=headers).json()[
            "reason_code"
        ]
        == "no_matching_paths"
    )
    assert _own_children() == children


def test_no_module_schedules_work_at_import_time() -> None:
    """Guard against a future ``asyncio.create_task`` at module scope.

    Import-time scheduling is invisible until something holds an event loop, so
    it is asserted structurally: a fresh interpreter imports every module with
    no running loop, which makes any module-level ``create_task`` raise, and
    then reports the threads and children the imports left behind.
    """
    import subprocess
    import sys

    probe = (
        "import threading, app.main, app.deps, app.routers.webhooks,"
        " app.routers.stream, app.routers.live, orchestrator.bus,"
        " orchestrator.pipeline, orchestrator.pool, orchestrator.triggers;"
        "print(threading.active_count())"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert out.stdout.strip() == "1"


def test_simkit_does_not_import_the_agent_layer() -> None:
    """The determinism boundary, expressed as a test.

    A simkit that reaches into the agent layer would also drag Devin's HTTP
    client into every worker process.
    """
    import subprocess
    import sys

    probe = (
        "import sys, simkit.runner, simkit.pool;"
        "bad=[m for m in sys.modules if m.startswith('orchestrator')];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**os.environ},
        check=True,
    )

    assert out.stdout.strip() == "[]"
