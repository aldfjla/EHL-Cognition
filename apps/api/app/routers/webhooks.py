"""``POST /webhooks/github`` — the entrypoint of the whole system.

Responsibility
--------------
Receive a push event from the customer repo, verify it, and start a pipeline
run. Nothing else — the handler must return immediately.

Inputs:  a GitHub push payload plus the ``X-Hub-Signature-256`` header.
Outputs: a created :class:`~orchestrator.schemas.Run` and a background task
         driving :class:`~orchestrator.pipeline.Pipeline`.

Why it returns immediately
--------------------------
GitHub times webhook deliveries out in seconds; a full run takes minutes. The
handler creates the run row, hands the pipeline to a background task, and
responds with the run id and its dashboard URL. All progress is observed over
the WebSocket, not the HTTP response.

Filtering
---------
Only pushes to ``TARGET_BRANCH`` of ``TARGET_REPO`` start a run. Everything else
gets a 200 with ``{"ignored": reason}`` — returning an error for events we
deliberately skip makes the customer's webhook page look broken.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.pipeline import Pipeline, PipelineContext
from orchestrator.schemas import EventType, Run, Scenario, ScenarioStatus, Stage
from orchestrator.workspace import Workspace
from sqlmodel import Session

from app import events
from app.config import Settings
from app.deps import get_bus, get_config, get_db, get_devin
from app.store import repo
from app.store.db import session_scope

log = logging.getLogger("robotci.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 check of ``X-Hub-Signature-256``.

    Must use :func:`hmac.compare_digest`; a naive ``==`` here is a timing oracle
    on the webhook secret.
    """
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _dashboard_url(settings: Settings, run_id: str) -> str:
    return f"{settings.ui_origin.rstrip('/')}/runs/{run_id}"


async def _drive_pipeline(
    run_id: str, bus: EventBus, settings: Settings, suite_size: int | None = None
) -> None:
    """Run the pipeline for ``run_id`` to a terminal stage.

    Any failure here is *our* failure, never a robot failure: it is recorded on
    ``run.error`` and published as an ``error`` event with ``fatal: true``, which
    the dashboard renders completely differently from a failed scenario.
    """
    with session_scope() as db:
        run = repo.get_run(db, run_id)
    if run is None:  # pragma: no cover - the caller just created it
        log.error("pipeline asked to drive unknown run %s", run_id)
        return

    # Construction is inside the try: a Workspace that cannot clone, or a
    # missing Devin key, is the same class of failure as a stage that throws.
    persistence: asyncio.Task[None] | None = None
    try:
        workspace = Workspace(
            run_id=run.id,
            repo=run.repo,
            commit_sha=run.commit_sha,
            root=Path("workspaces") / run.id,
        )
        ctx = PipelineContext(
            run=run,
            workspace=workspace,
            bus=bus,
            blackboard=Blackboard(run.id),
            devin=_devin_or_none(),
            max_fix_iterations=settings.max_agent_iterations,
            sim_workers=settings.sim_workers,
            suite_size=suite_size,
            default_suite_size=settings.suite_size,
        )
        persistence = asyncio.create_task(_persist_scenario_events(run.id, bus))
        await asyncio.sleep(0)
        await Pipeline(ctx).run()
    except Exception as exc:
        log.exception("run %s failed", run.id)
        await events.emit(
            bus,
            run.id,
            EventType.ERROR,
            {"stage": run.stage.value, "message": str(exc), "fatal": True},
        )
        with session_scope() as db:
            current = repo.get_run(db, run.id)
            if current is not None and not current.stage.is_terminal:
                repo.update_run(
                    db,
                    current.model_copy(
                        update={"stage": Stage.FAILED_UNRESOLVED, "error": str(exc)}
                    ),
                )
    finally:
        if persistence is not None:
            if not persistence.done():
                await bus.close(run.id)
            try:
                await asyncio.shield(persistence)
            except asyncio.CancelledError:
                persistence.cancel()
                await asyncio.gather(persistence, return_exceptions=True)
                raise


async def _persist_scenario_events(run_id: str, bus: EventBus) -> None:
    """Mirror live Scenario transitions into the API store.

    The orchestrator owns the event stream and cannot import the API store.
    Consuming the same stream here keeps REST's database-derived worker counts
    correct while a suite is running, including the transition to ``running``.
    """
    async for event in bus.subscribe(run_id):
        if event.type is EventType.SCENARIO_CREATED:
            scenario = Scenario(**event.data)
        elif event.type is EventType.SCENARIO_STARTED:
            with session_scope() as db:
                scenario = repo.get_scenario(db, str(event.data.get("scenario_id")))
                if scenario is None:
                    continue
                scenario = scenario.model_copy(
                    update={
                        "status": ScenarioStatus.RUNNING,
                        "worker_id": event.data.get("worker_id"),
                    }
                )
                repo.upsert_scenario(db, scenario)
            continue
        elif event.type is EventType.SCENARIO_FINISHED:
            scenario = Scenario(**event.data)
        else:
            continue
        with session_scope() as db:
            repo.upsert_scenario(db, scenario)


def _devin_or_none() -> Any:
    """The Devin client, or None when no key is configured.

    A missing key must not stop the API from booting or a seeded replay from
    running; it stops agent stages, which report it as an infrastructure error.
    """
    try:
        return get_devin()
    except RuntimeError as exc:
        log.warning("%s", exc)
        return None


async def _start_run(
    *,
    repo_name: str,
    sha: str,
    branch: str,
    commit_message: str,
    pushed_by: str,
    db: Session,
    bus: EventBus,
    settings: Settings,
    background: BackgroundTasks,
    suite_size: int | None = None,
) -> dict[str, Any]:
    """Create the run, announce it, and hand the pipeline to a background task."""
    existing = repo.find_active_run(db, repo_name, sha)
    if existing is not None:
        # GitHub redelivers; two pipelines racing on one repo fight over branches.
        return {
            "ignored": "a run for this commit is already in flight",
            "run_id": existing.id,
            "dashboard_url": _dashboard_url(settings, existing.id),
        }

    run = repo.create_run(
        db,
        Run(
            repo=repo_name,
            branch=branch,
            commit_sha=sha,
            commit_message=commit_message,
            pushed_by=pushed_by,
        ),
    )
    db.commit()

    await events.emit(bus, run.id, EventType.RUN_CREATED, run.model_dump(mode="json"))
    background.add_task(_drive_pipeline, run.id, bus, settings, suite_size=suite_size)

    return {"run_id": run.id, "dashboard_url": _dashboard_url(settings, run.id)}


@router.post("/github")
async def github_push(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    bus: EventBus = Depends(get_bus),
    settings: Settings = Depends(get_config),
) -> dict[str, Any]:
    """Handle a push event and start a run."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if settings.webhook_secret:
        if not verify_signature(body, signature, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")
    elif signature:
        log.warning("signed delivery received but WEBHOOK_SECRET is unset")

    try:
        payload: dict[str, Any] = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc

    event = request.headers.get("X-GitHub-Event", "push")
    if event != "push":
        return {"ignored": f"event {event} is not a push"}

    repo_name = str(payload.get("repository", {}).get("full_name") or "")
    ref = str(payload.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")

    if payload.get("deleted"):
        return {"ignored": "branch deletion"}

    head = payload.get("head_commit") or {}
    sha = str(payload.get("after") or head.get("id") or "")
    if not sha or set(sha) == {"0"}:
        return {"ignored": "no head commit"}

    connected = repo.get_repo_by_full_name(db, repo_name)
    if connected is not None:
        if branch != connected.branch:
            return {"ignored": f"{ref} is not connected branch {connected.branch}"}
        suite_size = connected.suite_size
        repo.update_repo(
            db,
            connected.model_copy(update={"last_push_at": datetime.now(UTC)}),
        )
    else:
        if settings.target_repo and repo_name != settings.target_repo:
            return {"ignored": f"{repo_name} is not TARGET_REPO"}
        if branch != settings.target_branch:
            return {"ignored": f"{ref} is not TARGET_BRANCH"}
        suite_size = None

    return await _start_run(
        repo_name=repo_name,
        sha=sha,
        branch=branch or settings.target_branch,
        commit_message=str(head.get("message") or ""),
        pushed_by=str(
            (payload.get("pusher") or {}).get("name")
            or (head.get("author") or {}).get("username")
            or ""
        ),
        db=db,
        bus=bus,
        settings=settings,
        background=background,
        suite_size=suite_size,
    )


@router.post("/manual")
async def manual_trigger(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    bus: EventBus = Depends(get_bus),
    settings: Settings = Depends(get_config),
) -> dict[str, Any]:
    """Start a run without GitHub. The demo and development path.

    Kept deliberately: a live webhook depends on a tunnel and someone else's
    infrastructure, and neither belongs on the critical path of a stage demo.

    ``repo``/``sha``/``branch`` are read from a JSON body, a form body or the
    query string, in that order — the README triggers this with a bare
    ``curl -d`` (which sends form encoding) and the dashboard sends JSON.
    """
    fields = await _trigger_fields(request)
    repo_name = fields.get("repo") or settings.target_repo
    sha = fields.get("sha") or ""
    if not repo_name or not sha:
        raise HTTPException(status_code=422, detail="repo and sha are required")

    return await _start_run(
        repo_name=repo_name,
        sha=sha,
        branch=fields.get("branch") or settings.target_branch,
        commit_message="manual trigger",
        pushed_by="manual",
        db=db,
        bus=bus,
        settings=settings,
        background=background,
    )


async def _trigger_fields(request: Request) -> dict[str, str]:
    """Extract ``repo``/``sha``/``branch`` from JSON, form or query params."""
    fields = {k: v for k, v in request.query_params.items() if v}
    body = await request.body()
    if not body:
        return fields

    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            fields.update({k: str(v) for k, v in parsed.items() if v is not None})
            return fields
    except ValueError:
        pass

    for part in body.decode(errors="replace").split("&"):
        key, _, value = part.partition("=")
        if key and value:
            fields[key] = value
    return fields
