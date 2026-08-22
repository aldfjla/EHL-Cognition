"""Cross-run memory: what is kept, what is dropped, how it is rendered."""

from __future__ import annotations

from typing import Any

import pytest
from orchestrator.devin import knowledge
from orchestrator.schemas import Finding, FindingKind, FindingStatus, Run, Speaker


def finding(
    kind: FindingKind,
    summary: str,
    status: FindingStatus = FindingStatus.CONFIRMED,
) -> Finding:
    return Finding(
        run_id="run-1",
        author_agent_id="agt-1",
        author_role=Speaker.INVESTIGATOR,
        kind=kind,
        summary=summary,
        status=status,
    )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture knowledge-API calls and serve a canned GET response."""
    state: dict[str, Any] = {"posts": [], "gets": 0, "existing": []}

    async def fake_request(
        method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if method == "GET":
            state["gets"] += 1
            return {"knowledge": state["existing"]}
        state["posts"].append(body or {})
        return {"id": f"note-{len(state['posts'])}"}

    monkeypatch.setattr(knowledge, "_request", fake_request)
    return state


async def test_recall_filters_by_repo(api: dict[str, Any]) -> None:
    api["existing"] = [
        {
            "name": "[robotci:acme/arm] constraint: joint 4 stop",
            "body": "joint 4 has a hard stop",
            "created_at": "2026-01-02",
        },
        {
            "name": "[robotci:other/repo] constraint: nope",
            "body": "irrelevant",
            "created_at": "2026-01-03",
        },
    ]
    assert await knowledge.recall("acme/arm") == ["joint 4 has a hard stop"]


async def test_harvest_keeps_constraints_and_drops_root_causes(
    api: dict[str, Any],
) -> None:
    run = Run(repo="acme/arm", commit_sha="abc")
    stored = await knowledge.harvest(
        run,
        [
            finding(FindingKind.CONSTRAINT, "joint 4 has a hard stop"),
            finding(FindingKind.ROOT_CAUSE, "gripper closes on a fixed timer"),
            finding(FindingKind.PATCH, "gate close on contact"),
            finding(FindingKind.OBSERVATION, "harness binds the entrypoint via a shim"),
            finding(FindingKind.OBSERVATION, "the cube was blue"),
        ],
    )
    names = " ".join(str(post.get("name", "")) for post in api["posts"])

    assert len(stored) == 2
    assert "hard stop" in names
    assert "shim" in names
    assert "timer" not in names, "root causes of fixed bugs must not persist"
    assert "blue" not in names


async def test_harvest_skips_unconfirmed_findings(api: dict[str, Any]) -> None:
    run = Run(repo="acme/arm", commit_sha="abc")
    stored = await knowledge.harvest(
        run,
        [finding(FindingKind.CONSTRAINT, "unverified", status=FindingStatus.PROPOSED)],
    )
    assert stored == []
    assert api["posts"] == []


async def test_harvest_dedupes_against_what_is_already_known(
    api: dict[str, Any],
) -> None:
    api["existing"] = [
        {
            "name": "[robotci:acme/arm] constraint: stop",
            "body": "joint 4 has a hard stop the urdf omits",
        }
    ]
    run = Run(repo="acme/arm", commit_sha="abc")
    stored = await knowledge.harvest(
        run,
        [
            finding(FindingKind.CONSTRAINT, "Joint 4 has a hard stop"),
            finding(FindingKind.CONSTRAINT, "Payload rating is 3 kg"),
        ],
    )
    assert len(stored) == 1
    assert "Payload" in api["posts"][0]["name"]


async def test_remember_tags_the_repo(api: dict[str, Any]) -> None:
    entry_id = await knowledge.remember(
        "acme/arm", finding(FindingKind.CONSTRAINT, "joint 4 stop")
    )
    assert entry_id == "note-1"
    post = api["posts"][0]
    assert "[robotci:acme/arm]" in post["name"]
    assert "[robotci:acme/arm]" in post["trigger_description"]


def test_render_preamble_headers_and_caps() -> None:
    assert knowledge.render_preamble([]) == ""
    rendered = knowledge.render_preamble(
        [f"fact {i}\nmore detail" for i in range(knowledge.MAX_PREAMBLE_ENTRIES + 3)]
    )
    assert rendered.startswith("## What we know about this repo")
    assert rendered.count("- fact") == knowledge.MAX_PREAMBLE_ENTRIES


def test_missing_key_is_a_devin_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    with pytest.raises(Exception, match="DEVIN_API_KEY"):
        knowledge._api()
