"""FastAPI application factory.

Responsibility
--------------
Wire the process together: mount routers, configure CORS for the dashboard,
expose ``/health``, and manage startup/shutdown singletons.

Inputs:  :class:`~app.config.Settings`.
Outputs: an ASGI app served by uvicorn (``make api``).

Routes
------
``GET  /health``                    liveness, used by scripts/dev.sh and tests
``POST /webhooks/github``           push trigger
``GET  /repos``, ``POST /repos``    connected repository management
``PATCH/DELETE /repos/{id}``        update or disconnect a repository
``GET  /runs``, ``/runs/{id}``      run list and detail
``GET  /runs/{id}/agents``          the team
``GET  /agents/{id}/messages``      one agent's relayed traffic
``GET  /artifacts/...``             videos, reports, diffs
``WS   /ws/runs/{id}``              the live event feed
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import deps
from app.config import get_settings, validate_paths
from app.routers import agents, artifacts, live, repos, runs, stream, webhooks
from app.store.db import get_engine


def create_app() -> FastAPI:
    """Build and configure the application.

    A factory rather than a module-level app so tests can construct isolated
    instances with overridden dependencies.
    """
    settings = get_settings()

    app = FastAPI(
        title="Robot CI",
        version="0.1.0",
        summary="Autonomous CI for robot control code, tested in simulation.",
        lifespan=deps.lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe. Intentionally does not touch the DB or Devin —
        it answers "is the process up", not "is everything configured"."""
        return {"status": "ok"}

    @app.get("/ready", tags=["meta"])
    async def ready() -> dict[str, Any]:
        """Readiness probe: the checks ``/health`` deliberately skips.

        Reports rather than refuses: the dashboard is useful with a missing
        model library or Devin key, so this returns the degraded detail and
        lets the caller decide.
        """
        checks: dict[str, Any] = {
            "database": _check_db(),
            "menagerie": settings.menagerie_dir.is_dir(),
            "devin_api_key": bool(settings.devin_api_key.strip()),
            "warnings": validate_paths(settings),
        }
        return {"status": "ok" if checks["database"] else "degraded", "checks": checks}

    for module in (repos, webhooks, runs, agents, artifacts, live, stream):
        app.include_router(module.router)

    return app


def _check_db() -> bool:
    """True when a trivial query round-trips to SQLite."""
    try:
        with get_engine().connect() as connection:
            connection.exec_driver_sql("SELECT 1")
    except Exception:  # noqa: BLE001 - the point of the probe is to report this
        return False
    return True


app = create_app()
