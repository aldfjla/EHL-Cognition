"""Connected repository management.

Repositories are dormant until a matching GitHub push reaches
``POST /webhooks/github``. Run history is retained when a repository is
disconnected.

A connected repository also carries the trigger filters the webhook evaluates
(``branches``, ``path_include``, ``path_exclude``). They are the registry's
cache of the customer repo's ``robotci.yaml`` ``ci:`` section, which cannot be
read at webhook time because nothing is cloned yet; stage TRIGGERED refreshes
them from the checkout. Setting them here marks ``filters_source`` as
``registry`` — until the next run reads the repo's committed config, which wins.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from orchestrator.schemas import Repo, RepoRunSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.config import Settings
from app.deps import get_config, get_db
from app.store import repo

router = APIRouter(prefix="/repos", tags=["repos"])

_FULL_NAME = re.compile(r"^[^/\s]+/[^/\s]+$")

#: Fields whose presence means the caller is asserting its own filters, so the
#: stored ``filters_source`` stops claiming they came from a checkout.
_FILTER_FIELDS = ("branch", "branches", "path_include", "path_exclude")


def _validate_full_name(value: str) -> str:
    value = value.strip()
    if not _FULL_NAME.fullmatch(value):
        raise ValueError("full_name must be in owner/name format")
    return value


class RepoCreate(BaseModel):
    """Request body for connecting a repository."""

    model_config = ConfigDict(extra="forbid")

    full_name: str
    branch: str = Field(default="main", min_length=1)
    suite_size: int = Field(default=50, ge=1)
    branches: list[str] = Field(default_factory=list)
    #: Omit to leave unset (built-in defaults apply); send ``[]`` to configure
    #: "no patterns" — the two are stored distinguishably.
    path_include: list[str] | None = None
    path_exclude: list[str] | None = None

    _full_name = field_validator("full_name")(_validate_full_name)


class RepoPatch(BaseModel):
    """Mutable connected repository settings."""

    model_config = ConfigDict(extra="forbid")

    branch: str | None = Field(default=None, min_length=1)
    suite_size: int | None = Field(default=None, ge=1)
    branches: list[str] | None = None
    path_include: list[str] | None = None
    path_exclude: list[str] | None = None

    @model_validator(mode="after")
    def reject_null_updates(self) -> RepoPatch:
        if "branch" in self.model_fields_set and self.branch is None:
            raise ValueError("branch cannot be null")
        if "suite_size" in self.model_fields_set and self.suite_size is None:
            raise ValueError("suite_size cannot be null")
        for name in _FILTER_FIELDS:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


def _with_run_summary(repository: Repo, db: Session) -> Repo:
    """Add derived status and latest-run information to a repository."""
    runs = repo.list_runs_for_repo(db, repository.full_name)
    latest = runs[0] if runs else None
    status = "running" if any(not run.stage.is_terminal for run in runs) else "dormant"
    return repository.model_copy(
        update={
            "status": status,
            "latest_run": (
                RepoRunSummary(
                    id=latest.id,
                    stage=latest.stage,
                    created_at=latest.created_at,
                )
                if latest
                else None
            ),
        }
    )


def _response(repository: Repo, db: Session) -> dict[str, Any]:
    return _with_run_summary(repository, db).model_dump(mode="json")


@router.get("")
async def list_connected_repos(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """List connected repositories with live status derived from run history."""
    return [_response(repository, db) for repository in repo.list_repos(db)]


@router.post("", status_code=201)
async def connect_repo(
    payload: RepoCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_config),
) -> dict[str, Any]:
    """Connect one repository and return webhook setup instructions."""
    if repo.get_repo_by_full_name(db, payload.full_name) is not None:
        raise HTTPException(status_code=409, detail="repository already connected")

    try:
        repository = repo.create_repo(
            db,
            Repo(
                full_name=payload.full_name,
                branch=payload.branch,
                suite_size=payload.suite_size,
                branches=payload.branches,
                path_include=payload.path_include,
                path_exclude=payload.path_exclude,
                filters_source=(
                    "registry"
                    if payload.model_fields_set & set(_FILTER_FIELDS)
                    else "default"
                ),
            ),
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="repository already connected"
        ) from exc

    return {
        "repo": _with_run_summary(repository, db).model_dump(mode="json"),
        "webhook": {
            "url": f"{settings.api_origin.rstrip('/')}/webhooks/github",
            "secret_configured": bool(settings.webhook_secret),
        },
    }


@router.delete("/{repo_id}", status_code=204)
async def disconnect_repo(repo_id: str, db: Session = Depends(get_db)) -> Response:
    """Disconnect a repository while retaining its historical runs."""
    if not repo.delete_repo(db, repo_id):
        raise HTTPException(status_code=404, detail="repository not found")
    return Response(status_code=204)


@router.patch("/{repo_id}")
async def update_connected_repo(
    repo_id: str,
    payload: RepoPatch,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update the branch or suite size used for future runs."""
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="at least one field is required")

    repository = repo.get_repo(db, repo_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="repository not found")

    if set(updates) & set(_FILTER_FIELDS):
        updates["filters_source"] = "registry"
    updated = repo.update_repo(db, repository.model_copy(update=updates))
    return _response(updated, db)
