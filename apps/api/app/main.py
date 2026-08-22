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
``GET  /runs``, ``/runs/{id}``      run list and detail
``GET  /runs/{id}/agents``          the team
``GET  /agents/{id}/messages``      one agent's relayed traffic
``GET  /artifacts/...``             videos, reports, diffs
``WS   /ws/runs/{id}``              the live event feed
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings


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

    # TODO(build): mount routers once they expose `router`:
    #   from app.routers import agents, artifacts, runs, stream, webhooks
    #   for m in (webhooks, runs, agents, artifacts, stream):
    #       app.include_router(m.router)
    # TODO(build): attach deps.lifespan to the FastAPI(...) constructor.
    # TODO(build): add a /ready endpoint that DOES check DB + menagerie + key.

    return app


app = create_app()
