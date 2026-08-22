"""Persistent memory across runs — what the team learned last time.

Responsibility
--------------
Devin's knowledge store lets facts survive a session. This module decides what
is worth keeping and injects it into future prompts.

Inputs:  confirmed findings from a finished run.
Outputs: knowledge entries scoped to a repo, and a prompt preamble for new runs.

What is worth remembering
-------------------------
Only things that are true *across* runs and expensive to rediscover:

* Which Menagerie model matched this repo's robot, and the confidence.
* The harness adapter shape that worked — rediscovering how to bind a
  controller to MuJoCo costs an agent several minutes every single run.
* Confirmed ``constraint`` findings ("this arm's joint 4 has a hard stop the
  URDF does not encode").
* Known-flaky scenario seeds.

Explicitly NOT remembered: root causes of fixed bugs. Once patched they are
noise, and stale causes actively mislead the next Investigator.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from orchestrator.devin.client import DevinError
from orchestrator.schemas import Finding, FindingKind, FindingStatus, Run

#: Prefix on every entry name so Robot CI's notes are recognisable in the UI.
NAME_PREFIX = "robotci"

#: Findings worth keeping across runs. Root causes are deliberately absent.
KEEP_KINDS = frozenset({FindingKind.CONSTRAINT, FindingKind.OBSERVATION})

#: Observations only survive when they describe something structural.
KEEP_OBSERVATION_PATTERN = re.compile(
    r"menagerie|model|harness|adapter|driver|flaky|seed", re.IGNORECASE
)

#: Cap on how much recalled knowledge is spliced into a prompt.
MAX_PREAMBLE_ENTRIES = 8


def _tag(repo: str) -> str:
    """The marker every entry for ``repo`` carries in its name."""
    return f"[{NAME_PREFIX}:{repo}]"


def _api() -> tuple[str, str]:
    """``(api_base, api_key)`` from the environment, or raise."""
    key = os.environ.get("DEVIN_API_KEY", "").strip()
    if not key:
        raise DevinError("DEVIN_API_KEY unset; copy .env.example to .env")
    base = os.environ.get("DEVIN_API_BASE", "https://api.devin.ai/v1").rstrip("/")
    return base, key


async def _request(
    method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One knowledge-API call. Raises :class:`DevinError` on any failure."""
    base, key = _api()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as http:
            response = await http.request(method, f"{base}{path}", json=body)
    except httpx.HTTPError as exc:
        raise DevinError(f"{method} {path} failed: {exc}") from exc
    if response.status_code >= 400:
        raise DevinError(
            f"{method} {path} -> HTTP {response.status_code}: {response.text[:300]}"
        )
    if not response.content:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {"data": payload}


def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The list of knowledge notes out of a GET /knowledge response."""
    for key in ("knowledge", "notes", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _render(finding: Finding) -> str:
    """Body text stored for one finding."""
    lines = [f"{finding.kind.value}: {finding.summary}"]
    if finding.detail:
        lines.append(finding.detail.strip())
    if finding.files:
        lines.append("Files: " + ", ".join(finding.files))
    lines.append(f"Confidence: {finding.confidence:.2f}")
    return "\n".join(lines)


async def recall(repo: str) -> list[str]:
    """Knowledge entries relevant to ``repo``, newest first."""
    payload = await _request("GET", "/knowledge")
    tag = _tag(repo)
    matching = [
        entry
        for entry in _entries(payload)
        if tag in str(entry.get("name", ""))
        or tag in str(entry.get("trigger_description", ""))
    ]
    matching.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
    return [
        str(entry.get("body", "")).strip() for entry in matching if entry.get("body")
    ]


async def remember(repo: str, finding: Finding) -> str:
    """Persist one finding as durable knowledge. Returns the entry id."""
    summary = finding.summary.strip().replace("\n", " ")
    payload = await _request(
        "POST",
        "/knowledge",
        {
            "name": f"{_tag(repo)} {finding.kind.value}: {summary[:80]}",
            "body": _render(finding),
            "trigger_description": (
                f"When running Robot CI against {repo} {_tag(repo)} — read before "
                f"resolving a model, building a harness or designing scenarios."
            ),
        },
    )
    entry_id = str(payload.get("id") or payload.get("knowledge_id") or "")
    if not entry_id:
        raise DevinError(f"POST /knowledge returned no id: {payload}")
    return entry_id


def _worth_keeping(finding: Finding) -> bool:
    """Apply this module's retention policy to one finding."""
    if finding.status != FindingStatus.CONFIRMED:
        return False
    if finding.kind not in KEEP_KINDS:
        return False
    if finding.kind is FindingKind.OBSERVATION:
        return bool(KEEP_OBSERVATION_PATTERN.search(finding.summary))
    return True


def _normalise(text: str) -> str:
    """Lowercased, whitespace-collapsed text for dedupe comparisons."""
    return re.sub(r"\s+", " ", text).strip().lower()


async def harvest(run: Run, findings: list[Finding]) -> list[str]:
    """Pick the keepers from a finished run and persist them.

    Applies the policy in this module's docstring — filters aggressively.
    """
    keepers = [f for f in findings if _worth_keeping(f)]
    if not keepers:
        return []
    try:
        known = {_normalise(entry) for entry in await recall(run.repo)}
    except DevinError:
        known = set()

    stored: list[str] = []
    seen: set[str] = set()
    for finding in keepers:
        summary = _normalise(finding.summary)
        if not summary or summary in seen:
            continue
        if any(summary in entry for entry in known):
            continue
        seen.add(summary)
        stored.append(await remember(run.repo, finding))
    return stored


def render_preamble(entries: list[str]) -> str:
    """Format recalled knowledge as a markdown block for a role prompt."""
    kept = [entry.strip() for entry in entries if entry and entry.strip()]
    if not kept:
        return ""
    lines = ["## What we know about this repo", ""]
    for entry in kept[:MAX_PREAMBLE_ENTRIES]:
        first, *rest = [line.strip() for line in entry.splitlines() if line.strip()]
        lines.append(f"- {first}")
        lines.extend(f"  {line}" for line in rest)
    return "\n".join(lines)
