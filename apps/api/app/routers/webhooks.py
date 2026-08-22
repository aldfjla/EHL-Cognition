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
A push starts a run only when it matches the target repository's *own* trigger
configuration: the pushed ref must match a watched branch pattern and at least
one changed path must match the path filters, so a README-only push does not
burn a run. Everything else gets a 200 with ``{"ignored": reason,
"reason_code": ...}`` — returning an error for events we deliberately skip
makes the customer's webhook page look broken.

Where the filters come from, honestly
-------------------------------------
The filters are declared in the customer's ``robotci.yaml`` under ``ci:``, and
that file **cannot be read here**: the repo is not cloned until stage
TRIGGERED, and cloning inside a webhook handler would blow GitHub's delivery
timeout. So the decision is made from the connected-repository registry, which
caches those keys, and stage TRIGGERED writes the checked-out ``ci:`` section
back into the registry (:mod:`orchestrator.triggers`). Consequence, stated
rather than hidden: a change to ``ci:`` takes effect from the *next* push, and
``Repo.filters_source`` says whether the filters used were the registry
defaults or the repo's committed config.

Every decision — start or ignore — is logged with the reason code, and every
ignore returns it.
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
from orchestrator import triggers
from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.pipeline import Pipeline, PipelineContext
from orchestrator.schemas import (
    Agent,
    EventType,
    Finding,
    Message,
    Report,
    Run,
    Scenario,
    ScenarioStatus,
    Stage,
)
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


def _ignored(
    reason: str, code: str, *, filters: triggers.Filters | None = None
) -> dict[str, Any]:
    """The body of a deliberate skip: 200, a sentence, and a stable code.

    ``filters`` is echoed when the skip was a filter decision, so the customer
    can see on GitHub's delivery page which patterns were applied instead of
    having to guess what Robot CI thinks it is watching.
    """
    log.info("ignored push: %s (%s)", reason, code)
    body: dict[str, Any] = {"ignored": reason, "reason_code": code}
    if filters is not None:
        body["filters"] = filters.as_dict()
    return body


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
            blackboard=Blackboard(run.id, bus),
            devin=_devin_or_none(),
            on_config=_filter_cache(run.repo),
            max_fix_iterations=settings.max_agent_iterations,
            sim_workers=settings.sim_workers,
            suite_size=suite_size,
            default_suite_size=settings.suite_size,
        )
        persistence = asyncio.create_task(_persist_run_events(run.id, bus))
        # Let persistence subscribe before a synchronously failing pipeline emits.
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
            await bus.close(run.id)
            driver = asyncio.current_task()
            if driver is not None and driver.cancelling():
                persistence.cancel()
            await persistence


def _filter_cache(repo_name: str) -> Any:
    """Callback that caches a checkout's ``ci:`` filters on the registry row.

    This is the only place the repo's *own* ``robotci.yaml`` can be read, so it
    is where the registry learns what the customer actually committed. The push
    that carried the change has already started; the cached filters apply from
    the next one.
    """

    async def cache(config: dict) -> None:
        filters = triggers.parse(config)
        if filters is None:
            log.info("%s has no robotci.yaml ci: section; filters unchanged", repo_name)
            return
        with session_scope() as db:
            connected = repo.get_repo_by_full_name(db, repo_name)
            if connected is None:
                log.info(
                    "%s is not a connected repository; ci: filters %s not cached",
                    repo_name,
                    filters.as_dict(),
                )
                return
            branches = list(filters.branches)
            repo.update_repo(
                db,
                connected.model_copy(
                    update={
                        "branch": branches[0],
                        "branches": branches[1:],
                        "path_include": list(filters.path_include),
                        "path_exclude": list(filters.path_exclude),
                        "filters_source": "robotci.yaml",
                    }
                ),
            )
        log.info("cached %s trigger filters from robotci.yaml: %s", repo_name, filters)

    return cache


async def _persist_run_events(run_id: str, bus: EventBus) -> None:
    """Mirror live run events into the API store on a best-effort basis.

    The orchestrator owns the event stream and cannot import the API store.
    Consuming the same stream here keeps REST's database-derived dashboard
    state current while a run is running, including agents and findings.
    """
    async for event in bus.subscribe(run_id):
        try:
            _persist_run_event(run_id, event.type, event.data, event.ts)
        except Exception as exc:  # noqa: BLE001 - mirroring must not stop a run
            log.warning(
                "persisting %s for run %s failed: %s",
                event.type.value,
                run_id,
                exc,
            )


def _persist_run_event(
    run_id: str, event_type: EventType, data: dict[str, Any], event_ts: datetime
) -> None:
    """Persist one event; callers isolate failures to this event."""
    if event_type is EventType.RUN_STAGE_CHANGED:
        with session_scope() as db:
            run = repo.get_run(db, run_id)
            if run is not None and data.get("stage"):
                repo.update_run(
                    db, run.model_copy(update={"stage": Stage(data["stage"])})
                )
        return

    if event_type is EventType.RUN_FINISHED:
        with session_scope() as db:
            repo.update_run(db, Run(**data))
        return

    if event_type is EventType.ERROR and data.get("fatal") is True:
        message = str(data.get("message") or "")
        if not message:
            return
        with session_scope() as db:
            run = repo.get_run(db, run_id)
            if run is not None and not run.error:
                repo.update_run(db, run.model_copy(update={"error": message}))
        return

    if event_type is EventType.SCENARIO_CREATED:
        with session_scope() as db:
            repo.upsert_scenario(db, Scenario(**data))
        return

    if event_type is EventType.SCENARIO_STARTED:
        with session_scope() as db:
            scenario = repo.get_scenario(db, str(data.get("scenario_id")))
            if scenario is not None:
                repo.upsert_scenario(
                    db,
                    scenario.model_copy(
                        update={
                            "status": ScenarioStatus.RUNNING,
                            "worker_id": data.get("worker_id"),
                        }
                    ),
                )
        return

    if event_type is EventType.SCENARIO_FINISHED:
        with session_scope() as db:
            repo.upsert_scenario(db, Scenario(**data))
        return

    if event_type is EventType.AGENT_CREATED:
        with session_scope() as db:
            repo.upsert_agent(db, Agent(**data))
        return

    if event_type in (
        EventType.AGENT_STATUS_CHANGED,
        EventType.AGENT_UPDATED,
        EventType.AGENT_ACTIVITY,
    ):
        agent_id = str(data.get("agent_id") or "")
        if not agent_id:
            return
        with session_scope() as db:
            agent = repo.get_agent(db, agent_id)
            if agent is None:
                return
            patch: dict[str, Any] = {}
            if event_type is EventType.AGENT_ACTIVITY:
                if "text" in data:
                    patch["last_activity"] = data["text"]
            else:
                fields = set(Agent.model_fields)
                patch = {
                    name: value
                    for name, value in data.items()
                    if name in fields and name != "id"
                }
            if patch:
                values = agent.model_dump(mode="json")
                values.update(patch)
                repo.upsert_agent(db, Agent.model_validate(values))
        return

    if event_type is EventType.MESSAGE_SENT:
        with session_scope() as db:
            repo.add_message(db, Message(**data))
        return

    if event_type in (EventType.FINDING_CREATED, EventType.FINDING_UPDATED):
        with session_scope() as db:
            if event_type is EventType.FINDING_CREATED:
                finding = Finding(**data)
            else:
                finding = repo.get_finding(db, str(data.get("finding_id")))
                if finding is None:
                    return
                patch = {
                    name: value
                    for name, value in data.items()
                    if name in Finding.model_fields and name != "id"
                }
                values = finding.model_dump(mode="json")
                values.update(patch)
                finding = Finding.model_validate(values)
            repo.upsert_finding(db, finding)
        return

    if event_type is EventType.REPORT_CREATED:
        with session_scope() as db:
            repo.save_report(db, Report(**data))


def _devin_or_none() -> Any:
    """The Devin client, or None when no key is configured.

    A missing key must not stop the API from booting; it stops agent stages,
    which report it as an infrastructure error.
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
            "reason_code": "already_in_flight",
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
        return _ignored(f"event {event} is not a push", "not_a_push")

    repo_name = str(payload.get("repository", {}).get("full_name") or "")
    ref = str(payload.get("ref") or "")
    branch = ref.removeprefix("refs/heads/")

    if payload.get("deleted"):
        return _ignored("branch deletion", "branch_deleted")

    head = payload.get("head_commit") or {}
    sha = str(payload.get("after") or head.get("id") or "")
    if not sha or set(sha) == {"0"}:
        return _ignored("no head commit", "no_head_commit")

    connected = repo.get_repo_by_full_name(db, repo_name)
    if connected is not None:
        filters = triggers.from_registry(
            branch=connected.branch,
            branches=connected.branches,
            path_include=connected.path_include,
            path_exclude=connected.path_exclude,
        )
        filters_source = connected.filters_source
        suite_size = connected.suite_size
    else:
        if settings.target_repo and repo_name != settings.target_repo:
            return _ignored(
                f"{repo_name} is not a connected repository and is not TARGET_REPO",
                "repo_not_connected",
            )
        # No registry row: fall back to the single-repo env configuration,
        # which is the demo/self-hosted path.
        filters = triggers.from_registry(branch=settings.target_branch)
        filters_source = "settings"
        suite_size = None

    paths = triggers.changed_paths(payload)
    decision = triggers.evaluate(filters, branch=branch, paths=paths)
    log.info(
        "push %s@%s ref=%s filters=%s source=%s -> %s: %s",
        repo_name,
        sha[:7],
        ref or "(none)",
        filters.as_dict(),
        filters_source,
        decision.code,
        decision.reason,
    )
    if not decision.start:
        return _ignored(decision.reason, decision.code, filters=filters)

    if connected is not None:
        repo.update_repo(
            db,
            connected.model_copy(update={"last_push_at": datetime.now(UTC)}),
        )

    started = await _start_run(
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
    return {**decision.as_dict(), **started}


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
