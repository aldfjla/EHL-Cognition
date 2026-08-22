"""Smoke tests: the app constructs and answers /health.

These run without a database, without Devin credentials and without MuJoCo, on
purpose. Their job is to catch the failure that wastes the most time — an import
error or a bad router mount that makes the whole process refuse to start — as
fast as possible.

Deeper tests belong next to what they test: pipeline transition tests in
``packages/orchestrator``, scoring and determinism tests in ``packages/simkit``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_app_constructs() -> None:
    """The factory builds an app without touching external services."""
    app = create_app()
    assert app.title == "Robot CI"


def test_health_returns_ok() -> None:
    """``GET /health`` is the liveness contract used by scripts/dev.sh."""
    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_orchestrator_and_simkit_import() -> None:
    """The stub tree has no broken imports.

    Cheap insurance: every module below is imported by the pipeline at runtime,
    and a typo in any of them would otherwise surface mid-demo.
    """
    import orchestrator.blackboard
    import orchestrator.bus
    import orchestrator.clustering
    import orchestrator.pipeline
    import orchestrator.schemas  # noqa: F401
    import simkit.runner
    import simkit.scoring
    import simkit.suite  # noqa: F401


def test_stage_machine_is_wired() -> None:
    """Every stage has a transition entry and the terminals are terminal.

    Guards the one piece of real logic in the scaffold: a stage added to the
    enum without an entry in TRANSITIONS would strand a run silently.
    """
    from orchestrator.pipeline import TRANSITIONS
    from orchestrator.schemas import TERMINAL_STAGES, Stage

    for stage in Stage:
        assert stage in TRANSITIONS, f"{stage} missing from TRANSITIONS"

    for stage in TERMINAL_STAGES:
        assert TRANSITIONS[stage] == (), f"{stage} is terminal but has exits"
        assert stage.is_terminal
