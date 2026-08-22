"""Measure what the API costs while asleep, and how fast it wakes.

Two numbers back the dormancy claim, and both are measured here rather than
asserted:

* **idle** — RSS, CPU time, threads, child processes and open sockets of a real
  uvicorn process with no run in flight, sampled over a window.
* **cold start** — wall time from writing the first byte of a signed push
  delivery to a WebSocket subscriber receiving the first event of the run.

Usage::

    .venv/bin/python scripts/measure_dormancy.py [--idle-seconds 60]

Reads nothing from the network. Writes JSON to stdout; ``docs/DORMANCY.md``
quotes a run of it. Anything the environment cannot report is emitted as
``null`` with a note — never as a plausible-looking number.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "measure-me"
CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _proc_status(pid: int) -> dict[str, Any]:
    """RSS/threads/CPU/fd counts from /proc, or nulls off Linux."""
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return {
            "unavailable": "procfs is not present on this platform",
            "rss_mb": None,
            "threads": None,
            "cpu_seconds": None,
            "open_sockets": None,
            "child_processes": None,
        }
    fields = dict(
        line.split(":", 1)
        for line in status_path.read_text().splitlines()
        if ":" in line
    )
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[-1].split()
    utime, stime = int(stat[11]), int(stat[12])
    fds = list(Path(f"/proc/{pid}/fd").iterdir())
    sockets = 0
    for fd in fds:
        try:
            if "socket:" in os.readlink(fd):
                sockets += 1
        except OSError:
            continue
    children: set[str] = set()
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            children.update((task / "children").read_text().split())
        except OSError:
            continue
    return {
        "rss_mb": round(int(fields["VmRSS"].split()[0]) / 1024, 1),
        "threads": int(fields["Threads"]),
        "cpu_seconds": round((utime + stime) / CLOCK_TICKS, 3),
        "open_sockets": sockets,
        "child_processes": len(children),
    }


def _wait_for_health(port: int, deadline_s: float = 30.0) -> float:
    """Block until ``/health`` answers; return the seconds it took."""
    import urllib.error
    import urllib.request

    start = time.perf_counter()
    while time.perf_counter() - start < deadline_s:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return time.perf_counter() - start
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.05)
    raise RuntimeError("the API did not become healthy")


def _signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }


async def _cold_start(port: int, sha: str) -> dict[str, Any]:
    """Time from delivering a push to the first event on a subscriber socket.

    The subscriber attaches to the run *index* stream before the delivery,
    because the run id does not exist until the handler creates it — which is
    exactly the path a dashboard that is already open takes.
    """
    import httpx
    import websockets
    from websockets.exceptions import ConnectionClosed

    payload = {
        "ref": "refs/heads/main",
        "after": sha,
        "repository": {"full_name": "acme/robot"},
        "pusher": {"name": "measure"},
        "head_commit": {
            "id": sha,
            "message": "measure cold start",
            "modified": ["src/controller.py"],
        },
    }
    body, headers = _signed(payload)

    async with websockets.connect(f"ws://127.0.0.1:{port}/ws/runs") as socket_:
        await asyncio.sleep(0.2)  # let the subscription register
        async with httpx.AsyncClient() as http:
            started = time.perf_counter()
            response = await http.post(
                f"http://127.0.0.1:{port}/webhooks/github",
                content=body,
                headers=headers,
            )
            responded = time.perf_counter()
            frame = await asyncio.wait_for(socket_.recv(), timeout=30)
            first_event = time.perf_counter()
    # The index socket only carries run-level events. Attach to the run's own
    # topic to see how far the pipeline gets: the first stage event is the
    # honest "the system is awake and working" marker.
    trailing: list[dict[str, Any]] = []
    run_id = response.json().get("run_id")
    if run_id:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ws/runs/{run_id}"
        ) as run_socket:
            while len(trailing) < 6:
                try:
                    extra = await asyncio.wait_for(run_socket.recv(), timeout=8)
                except (TimeoutError, ConnectionClosed):
                    break  # the server closes the socket after run.finished
                parsed = json.loads(extra)
                trailing.append(
                    {
                        "type": parsed.get("type"),
                        "at_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                )

    event = json.loads(frame)
    return {
        "http_response_ms": round((responded - started) * 1000, 1),
        "first_event_ms": round((first_event - started) * 1000, 1),
        "first_event_type": event.get("type"),
        "webhook_reason_code": response.json().get("reason_code"),
        "following_events": trailing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idle-seconds", type=float, default=60.0)
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="robotci-measure-"))
    port = _free_port()
    env = {
        **os.environ,
        "WEBHOOK_SECRET": SECRET,
        "DATABASE_URL": f"sqlite:///{workdir / 'robotci.db'}",
        "ARTIFACTS_DIR": str(workdir / "artifacts"),
        "DEVIN_API_KEY": "",
        "PYTHONUNBUFFERED": "1",
    }
    api = subprocess.Popen(
        [
            str(REPO_ROOT / ".venv/bin/uvicorn"),
            "app.main:app",
            "--app-dir",
            "apps/api",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        boot_s = _wait_for_health(port)
        settled = _proc_status(api.pid)
        time.sleep(args.idle_seconds)
        after_idle = _proc_status(api.pid)

        cpu_delta = None
        if settled.get("cpu_seconds") is not None:
            cpu_delta = round(
                after_idle["cpu_seconds"] - settled["cpu_seconds"],  # type: ignore[operator]
                3,
            )

        cold = asyncio.run(_cold_start(port, "c" * 40))
        time.sleep(5)
        after_run = _proc_status(api.pid)
        report = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "boot_to_health_s": round(boot_s, 3),
            "idle_window_s": args.idle_seconds,
            "idle_at_settle": settled,
            "idle_after_window": after_idle,
            "idle_cpu_seconds_consumed": cpu_delta,
            "cold_start": cold,
            "after_the_run_settled": after_run,
        }
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            api.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
