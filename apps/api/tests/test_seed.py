"""``make seed`` must produce a run the dashboard can be built against.

Runs the generator in ``--instant`` mode against the temporary database, then
asserts through the REST API — the same way the UI sees it. This is the test
that keeps the credential-free development path working.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient

SEED_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed_mock_run.py"


def load_seed_module() -> ModuleType:
    """Import the seed script by path — ``scripts/`` is not a package."""
    spec = importlib.util.spec_from_file_location("seed_mock_run", SEED_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_instant_seed_produces_a_full_run(client: TestClient) -> None:
    # An unroutable API base keeps this store-only: publishing to a live bus is
    # the seed script's other mode and is not what this test is about.
    seed = load_seed_module()
    assert seed.main(["--instant", "--api-base", "http://127.0.0.1:1"]) == 0

    runs = client.get("/runs").json()
    assert len(runs) == 1
    run_id = runs[0]["id"]
    assert runs[0]["stage"] == "PR_OPENED"
    assert runs[0]["commit_message"].startswith("[REPLAY]")

    detail = client.get(f"/runs/{run_id}").json()
    baseline = [
        scenario for scenario in detail["scenarios"] if scenario["attempt"] == 1
    ]
    assert len(baseline) == 24
    assert sum(1 for s in baseline if s["status"] == "failed") > 0
    assert len(detail["clusters"]) >= 1

    assert len(client.get(f"/runs/{run_id}/agents").json()) >= 4
    assert len(client.get(f"/runs/{run_id}/messages").json()) >= 1
    assert len(client.get(f"/runs/{run_id}/findings").json()) >= 1

    report = client.get(f"/runs/{run_id}/report").json()
    assert report["verdict"] == "fixed"
    assert report["after"]["failed"] == 0
