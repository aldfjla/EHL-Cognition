"""Relay filtering — the rules in ``docs/AGENT_ROLES.md``."""

from __future__ import annotations

from orchestrator.blackboard import Blackboard
from orchestrator.bus import EventBus
from orchestrator.schemas import (
    EventType,
    Finding,
    FindingKind,
    FindingStatus,
    Role,
    Speaker,
)

RUN = "run_bb"


def finding(
    kind: FindingKind,
    role: Speaker,
    summary: str = "s",
    confidence: float = 0.5,
    cluster_id: str | None = None,
) -> Finding:
    return Finding(
        run_id=RUN,
        author_role=role,
        author_agent_id="agt_1",
        kind=kind,
        summary=summary,
        confidence=confidence,
        cluster_id=cluster_id,
    )


async def test_write_indexes_and_emits_finding_created() -> None:
    bus = EventBus()
    board = Blackboard(RUN, bus)
    written = await board.write(
        finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR, cluster_id="cls_1")
    )

    assert board.all() == [written]
    assert board.for_cluster("cls_1") == [written]
    [event] = bus.history(RUN)
    assert event.type is EventType.FINDING_CREATED
    assert event.data["id"] == written.id


async def test_confirm_refute_and_supersede_emit_updates_and_keep_history() -> None:
    bus = EventBus()
    board = Blackboard(RUN, bus)
    first = await board.write(finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR))
    wrong = await board.write(finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR))

    await board.confirm(first.id, "agt_reviewer")
    await board.refute(wrong.id, "patch did not hold")
    better = finding(FindingKind.ROOT_CAUSE, Speaker.FIXER, summary="better")
    await board.supersede(first.id, better)

    assert first.status is FindingStatus.SUPERSEDED
    assert first.superseded_by == better.id
    assert wrong.status is FindingStatus.REFUTED
    # Nothing is ever removed: the board is the audit trail.
    assert [f.id for f in board.all()] == [first.id, wrong.id, better.id]
    updates = [e for e in bus.history(RUN) if e.type is EventType.FINDING_UPDATED]
    assert [e.data["finding_id"] for e in updates] == [first.id, wrong.id, first.id]
    assert updates[-1].data["superseded_by"] == better.id


async def test_relay_policy_per_role() -> None:
    board = Blackboard(RUN)
    modeler_constraint = await board.write(
        finding(FindingKind.CONSTRAINT, Speaker.MODELER, "7 dof arm")
    )
    harness_constraint = await board.write(
        finding(FindingKind.CONSTRAINT, Speaker.HARNESS_BUILDER, "10 hz control")
    )
    observation = await board.write(
        finding(FindingKind.OBSERVATION, Speaker.INVESTIGATOR, "slips when light")
    )
    review_note = await board.write(
        finding(FindingKind.VERIFICATION, Speaker.REVIEWER, "patch regressed 3")
    )

    # Modeler is first on the board — nothing to relay.
    assert board.for_role(Role.MODELER) == []
    # The harness only cares about what the Modeler established about hardware.
    assert board.for_role(Role.HARNESS_BUILDER) == [modeler_constraint]
    # Constraints are globally visible downstream.
    assert board.for_role(Role.SCENARIO_DESIGNER) == [
        modeler_constraint,
        harness_constraint,
    ]
    investigator = board.for_role(Role.INVESTIGATOR)
    assert observation in investigator
    assert review_note not in investigator
    fixer = board.for_role(Role.FIXER)
    assert review_note in fixer
    # Another cluster's observation is not the Fixer's business.
    assert observation not in fixer
    # The Reviewer arbitrates, so it sees everything.
    assert board.for_role(Role.REVIEWER) == board.all()


async def test_reporter_sees_only_confirmed_findings() -> None:
    board = Blackboard(RUN)
    confirmed = await board.write(finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR))
    await board.write(finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR))
    await board.confirm(confirmed.id, "agt_reviewer")

    assert board.for_role(Role.REPORTER) == [confirmed]


async def test_confirmed_root_causes_are_sorted_by_confidence() -> None:
    board = Blackboard(RUN)
    low = await board.write(
        finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR, confidence=0.4)
    )
    high = await board.write(
        finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR, confidence=0.9)
    )
    proposed = await board.write(
        finding(FindingKind.ROOT_CAUSE, Speaker.INVESTIGATOR, confidence=1.0)
    )
    await board.confirm(low.id, "agt")
    await board.confirm(high.id, "agt")

    assert board.confirmed_root_causes() == [high, low]
    assert proposed not in board.confirmed_root_causes()


async def test_render_context_skips_superseded_and_stays_bounded() -> None:
    board = Blackboard(RUN)
    stale = await board.write(
        finding(FindingKind.CONSTRAINT, Speaker.MODELER, "stale constraint")
    )
    fresh = finding(FindingKind.CONSTRAINT, Speaker.MODELER, "fresh constraint")
    await board.supersede(stale.id, fresh)

    context = board.render_context(Role.SCENARIO_DESIGNER)
    assert "fresh constraint" in context
    assert "stale constraint" not in context


async def test_render_context_is_empty_when_nothing_is_relayable() -> None:
    board = Blackboard(RUN)
    await board.write(finding(FindingKind.OBSERVATION, Speaker.INVESTIGATOR))
    assert board.render_context(Role.MODELER) == ""
